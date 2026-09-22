#!/usr/bin/env python3
"""Hermetic Codex family selection: fixture catalogs, no CLI, network or inference."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from profiles import load_profiles
from test_profile_sync import CODEX_TOKEN, args, command, flag, run_workflows

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
SELECT = HERE / "codex-select.py"
ADAPTER = HERE / "agents.sh"
spec = importlib.util.spec_from_file_location("codex_select", SELECT)
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

ALL = ("low", "medium", "high", "xhigh")


def row(model, hidden=False, efforts=ALL):
    return {"id": model, "model": model, "displayName": model, "hidden": hidden,
            "defaultReasoningEffort": efforts[0],
            "supportedReasoningEfforts": [{"reasoningEffort": e, "description": e} for e in efforts]}


def envelope(*rows, complete=True):
    return json.dumps({"models": list(rows), "complete": complete, "authoritative": False,
                       "source": "codex app-server model/list", "reason": "fixture"})


# The native catalog observed on 2026-09-22 (codex-cli 0.155.1): no gpt-6-terra.
OBSERVED = envelope(row("gpt-6-astra"), row("gpt-6-sol"), row("gpt-6-luna"),
                    row("gpt-reserve", hidden=True), row("gpt-5.6-sol"),
                    row("gpt-5.6-terra"), row("gpt-5.6-luna"), row("gpt-5.5"),
                    row("codex-auto-review", hidden=True))


def run_select(stdin, *argv):
    proc = subprocess.run([sys.executable, str(SELECT), *argv], input=stdin,
                          capture_output=True, text=True, timeout=10)
    out = dict(line.split("=", 1) for line in proc.stdout.splitlines())
    return proc.returncode, out


class GrammarTests(unittest.TestCase):
    def test_eligible_ids_and_numeric_order(self):
        self.assertEqual(cs.family_version("gpt-6-sol"), ("sol", (6, 0, 0)))
        self.assertEqual(cs.family_version("gpt-5.6-terra"), ("terra", (5, 6, 1)))
        self.assertGreater(cs.family_version("gpt-6.10-sol")[1], cs.family_version("gpt-6.9-sol")[1])
        self.assertGreater(cs.family_version("gpt-7-sol")[1], cs.family_version("gpt-6.99-sol")[1])
        self.assertGreater(cs.family_version("gpt-6.1-luna")[1], cs.family_version("gpt-6-luna")[1])
        # Both spellings of x.0 listed: deterministic, explicit minor wins.
        self.assertGreater(cs.family_version("gpt-6.0-sol")[1], cs.family_version("gpt-6-sol")[1])

    def test_rejected_variants(self):
        for bad in ("gpt-6-sol-2026-09-01", "gpt-6-sol-preview", "gpt-6-sol-fast",
                    "gpt-6-codex-spark", "gpt-6-sol-spark", "gpt-6-lunar", "gpt-6-Sol",
                    "gpt-5.5", "gpt-reserve", "o4-sol", "gpt-06-sol", "gpt-6.01-sol",
                    "gpt-1000-sol", "gpt-6.1000-sol", "gpt-6.-sol", "gpt--sol", "gpt-6.1.2-sol",
                    "gpt-6-sol\n", "gpt-6\x00-sol", " gpt-6-sol", "gpt-6-sol ", "", None, 6):
            with self.subTest(bad=bad):
                self.assertIsNone(cs.family_version(bad))


class SelectionTests(unittest.TestCase):
    def pick(self, catalog, family, effort="medium", pin=None):
        rows = cs.parse_catalog(catalog)
        return cs.select(family, pin, effort, rows, "complete")

    def test_observed_catalog_keeps_mixed_generations(self):
        got = {f: self.pick(OBSERVED, f) for f in cs.FAMILIES}
        self.assertEqual({f: r["selected"] for f, r in got.items()},
                         {"astra": "gpt-6-astra", "sol": "gpt-6-sol",
                          "terra": "gpt-5.6-terra", "luna": "gpt-6-luna"})
        for family, result in got.items():
            self.assertEqual(result["source"], "catalog-latest", family)
            self.assertEqual(result["family"], family)
            self.assertEqual(result["degraded"], "")

    def test_future_releases_need_no_edit(self):
        catalog = envelope(row("gpt-6-sol"), row("gpt-6.9-sol"), row("gpt-6.10-sol"),
                           row("gpt-7-astra"), row("gpt-6-astra"), row("gpt-7-sol-preview"),
                           row("gpt-8-sol-2027-01-01"), row("gpt-9-codex-spark"))
        self.assertEqual(self.pick(catalog, "sol")["selected"], "gpt-6.10-sol")
        self.assertEqual(self.pick(catalog, "astra")["selected"], "gpt-7-astra")

    def test_no_cross_family_borrowing(self):
        # Terra is absent: never Sol's or Astra's newer model instead.
        catalog = envelope(row("gpt-7-sol"), row("gpt-7-astra"))
        result = self.pick(catalog, "terra")
        self.assertEqual(result["source"], "fallback")
        self.assertEqual(result["selected"], cs.FLOOR["terra"])
        self.assertIn("no terra model", result["degraded"])
        self.assertEqual(result["listed"], "no")

    def test_hidden_models_are_not_family_candidates(self):
        catalog = envelope(row("gpt-5.6-sol"), row("gpt-9-sol", hidden=True))
        self.assertEqual(self.pick(catalog, "sol")["selected"], "gpt-5.6-sol")

    def test_known_effort_support_is_preserved(self):
        catalog = envelope(row("gpt-7-sol", efforts=("low", "medium")), row("gpt-6-sol"))
        result = self.pick(catalog, "sol", effort="xhigh")
        self.assertEqual((result["selected"], result["source"], result["latest_candidate"]),
                         ("gpt-6-sol", "older-compatible", "gpt-7-sol"))
        self.assertIn("gpt-7-sol does not list effort xhigh", result["degraded"])
        none = self.pick(envelope(row("gpt-7-sol", efforts=("low",))), "sol", effort="xhigh")
        self.assertEqual(none["selected"], "")
        # No effort requested: support is unknown, never invented.
        self.assertEqual(self.pick(OBSERVED, "sol", effort=None)["effort_supported"], "unknown")

    def test_missing_effort_metadata_is_unknown_not_permission(self):
        rows = [{"model": "gpt-6-sol", "hidden": False, "efforts": None}]
        result = cs.select("sol", None, "medium", rows, "complete")
        self.assertEqual((result["selected"], result["effort_supported"]), ("gpt-6-sol", "unknown"))

    def test_pins_stay_exact(self):
        for pin, listed in (("gpt-5.6-sol", "yes"), ("gpt-9-sol", "no"), ("my-alias", "no")):
            with self.subTest(pin=pin):
                result = self.pick(OBSERVED, "sol", pin=pin)
                self.assertEqual((result["selected"], result["source"], result["listed"]),
                                 (pin, "pinned", listed))
        self.assertEqual(self.pick(OBSERVED, "sol", pin="my-alias")["family"], "custom")
        self.assertIn("not in the advisory catalog", self.pick(OBSERVED, "sol", pin="gpt-9-sol")["degraded"])
        # Pin + unusable catalog: still the pin, never the floor.
        offline = cs.select("sol", "gpt-9-sol", "medium", None, "unavailable")
        self.assertEqual((offline["selected"], offline["listed"]), ("gpt-9-sol", "unknown"))

    def test_catalog_states(self):
        for text in ("", "not json", envelope(row("gpt-6-sol"), complete=False),
                     json.dumps({"complete": True, "models": [{"model": "bad id"}]}),
                     json.dumps({"complete": True, "models": "x"}), "[]"):
            with self.subTest(text=text[:40]):
                with self.assertRaises((ValueError, TypeError)):
                    cs.parse_catalog(text)
        rc, out = run_select("garbage", "--family", "sol")
        self.assertEqual((rc, out["catalog"], out["source"], out["selected"]),
                         (0, "malformed", "fallback", cs.FLOOR["sol"]))
        rc, out = run_select(OBSERVED, "--family", "sol", "--catalog-rc", "124")
        self.assertEqual((out["catalog"], out["source"], out["listed"]), ("unavailable", "fallback", "unknown"))
        self.assertIn("not verified latest", out["degraded"])
        rc, out = run_select(envelope(), "--family", "luna")
        self.assertEqual((out["catalog"], out["source"]), ("complete", "fallback"))

    def test_cli_output_is_single_line_data(self):
        rc, out = run_select(OBSERVED, "--family", "terra", "--effort", "medium")
        self.assertEqual(rc, 0)
        self.assertEqual(out["selected"], "gpt-5.6-terra")
        self.assertEqual(out["requested"], "family:terra")
        bad = subprocess.run([sys.executable, str(SELECT), "--family", "lunar"], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        bad = subprocess.run([sys.executable, str(SELECT), "--family", "sol", "--pin", "$(x)"], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)

    def test_no_stale_cache_or_second_app_server_client(self):
        # Selection consumes codex-models.py output only; a models_cache.json can
        # omit a generation the live catalog lists (observed 2026-09-22).
        for path in (SELECT, ADAPTER):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("models_cache", text)
        self.assertNotIn("app-server", SELECT.read_text(encoding="utf-8").split('"""', 2)[2])


def adapter(script, catalog, rc=0, env=None):
    """Source agents.sh with the catalog probe replaced by a fixture."""
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp) / "catalog.json"
        fixture.write_text(catalog, encoding="utf-8")
        prelude = (f'source {ADAPTER!s}\n'
                   f'_codex_load_models() {{ _codex_models_done=1; _codex_catalog_raw="$(cat {fixture})"; _codex_catalog_rc={rc}; }}\n')
        full_env = {k: v for k, v in os.environ.items() if not k.startswith("SWARM_")}
        full_env.update(env or {})
        return subprocess.run(["/bin/bash", "-c", prelude + script], capture_output=True,
                              text=True, timeout=20, env=full_env)


class AdapterTests(unittest.TestCase):
    def kv(self, text):
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

    def test_codex_model_subcommand(self):
        proc = adapter("subcmd_codex_model --family terra --effort medium", OBSERVED)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = self.kv(proc.stdout)
        self.assertEqual((out["selected"], out["family"], out["effort"]), ("gpt-5.6-terra", "terra", "medium"))

    def test_operator_pin_wins_and_stays_exact(self):
        proc = adapter("subcmd_codex_model --family astra", OBSERVED, env={"SWARM_CODEX_MODEL": "gpt-9-sol"})
        out = self.kv(proc.stdout)
        self.assertEqual((out["selected"], out["source"], out["listed"]), ("gpt-9-sol", "pinned", "no"))
        proc = adapter("subcmd_codex_model --family sol", OBSERVED, env={"SWARM_CODEX_MODEL": "$(touch x)"})
        self.assertEqual(proc.returncode, 2)

    def test_readiness_uses_the_same_selector(self):
        proc = adapter('codex_model_offered ""; echo "hint=$_codex_model_hint"; echo "sel=$(_kv selected "$CODEX_SEL")"', OBSERVED)
        out = self.kv(proc.stdout)
        self.assertEqual((out["sel"], out["hint"]), ("gpt-6-sol", ""))
        proc = adapter('codex_model_offered gpt-9-sol; echo "hint=$_codex_model_hint"', OBSERVED)
        self.assertIn("gpt-9-sol: model availability unverified", self.kv(proc.stdout)["hint"])
        self.assertIn("auth-only readiness", self.kv(proc.stdout)["hint"])
        proc = adapter('codex_model_offered ""; echo "hint=$_codex_model_hint"', "", rc=124)
        self.assertIn("catalog unavailable", self.kv(proc.stdout)["hint"])

    def test_default_family_matches_the_default_profile(self):
        profiles = load_profiles(PLUGIN / "workflows/swarm-review.js")
        default = re.search(r'^CODEX_DEFAULT_FAMILY="(\w+)"', ADAPTER.read_text(encoding="utf-8"), re.M).group(1)
        self.assertEqual(default, profiles["default"]["externals"]["codex"]["family"])
        self.assertEqual({p["externals"]["codex"]["family"] for p in profiles.values()}, {"sol", "astra"})


class HandoffTests(unittest.TestCase):
    """The frozen token reaches every voice unchanged; nothing re-resolves."""

    def voices(self, output):
        return [c for c in output["calls"] if c["opts"]["label"].startswith("codex:")]

    def test_one_model_across_voices_labels_and_resume(self):
        token = ("selected=gpt-5.6-terra;family=terra;source=catalog-latest;"
                 "latest_candidate=gpt-5.6-terra;catalog=complete;effort=medium")
        first, resumed = run_workflows([{"args": args(profile=name, codex=token, claude=False,
                                                       externalVoices=["codex"])} for name in ("max", "max")])
        for output in (first, resumed):
            models = {flag(command(c), "--model") for c in self.voices(output)}
            self.assertEqual(models, {"gpt-5.6-terra"})
            self.assertEqual(len(self.voices(output)), 11)
            balance = output["result"]["balance"]
            self.assertEqual(balance["codexModel"]["model"], "gpt-5.6-terra")
            self.assertEqual(balance["codexModel"]["family"], "terra")
            agents = {a["backend"]: a for a in balance["agents"]}
            self.assertEqual(agents["codex"]["model"], "gpt-5.6-terra")
            self.assertTrue(any("codex model for this run: gpt-5.6-terra" in line for line in output["logs"]))
        self.assertEqual([c["prompt"] for c in self.voices(first)], [c["prompt"] for c in self.voices(resumed)])

    def test_missing_or_invalid_token_drops_codex(self):
        cases = [{"args": args(codex=value, externalVoices=["codex", "grok"])}
                 for value in ("", "selected=", "selected=$(touch x)", "selected=a b", None)]
        for output in run_workflows(cases):
            self.assertEqual(self.voices(output), [])
            self.assertTrue(output["result"]["balance"]["codexDropped"])
            self.assertIsNone(output["result"]["balance"]["codexModel"])
            self.assertTrue(any("codex DROPPED" in line for line in output["logs"]))

    def test_degraded_source_is_carried_not_upgraded(self):
        token = CODEX_TOKEN.replace("source=catalog-latest", "source=fallback").replace("catalog=complete", "catalog=unavailable")
        output = run_workflows([{"args": args(codex=token, externalVoices=["codex"])}])[0]
        model = output["result"]["balance"]["codexModel"]
        self.assertEqual((model["source"], model["catalog"]), ("fallback", "unavailable"))


if __name__ == "__main__":
    unittest.main()
