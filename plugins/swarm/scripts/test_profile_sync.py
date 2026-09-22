#!/usr/bin/env python3
"""Hermetic profile contracts: real workflow calls, staged accessor, skill guards."""
from __future__ import annotations

import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from profiles import BEGIN, END, NAMES, STAGES, load_profiles, profile_name

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
WORKFLOW = PLUGIN / "workflows/swarm-review.js"
SKILL = PLUGIN / "skills/review/SKILL.md"
SOURCE = WORKFLOW.read_text(encoding="utf-8")
PROFILES = load_profiles(WORKFLOW)

# Execute the complete shipped workflow with only agent/parallel/phase/log stubbed.
# No backend CLI, network or actual Workflow invocation is involved.
NODE = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8').replace('export const meta', 'const meta');
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
(async () => {
  const output = [];
  for (const test of cases) {
    const calls = [], logs = [];
    let found = false;
    const finding = {file:'fixture.js', line:1, severity:'warning', summary:'[correctness] fixture',
      failure_scenario:'fixture fails', confidence:'high', recommendation:'fix fixture'};
    const agent = async (prompt, opts) => {
      calls.push({prompt, opts});
      if (opts.phase === 'Scope') return {change_kind:'fixture', run:test.gate || [], skip:[]};
      if (opts.phase === 'Merge') return {clusters:[]};
      if (opts.phase === 'Verify') return {verdict:'CONFIRMED', evidence:'fixture'};
      if (test.voiceResults && Object.hasOwn(test.voiceResults, opts.label)) return test.voiceResults[opts.label];
      const findings = found ? [] : [finding]; found = true;
      return opts.label.startsWith('claude:') ? {findings} : {ok:true,error:'',findings};
    };
    let body = source;
    if (test.patch) body = body.replace('const PROFILE = PROFILES[PROFILE_NAME]',
      'const PROFILE = PROFILES[PROFILE_NAME]; Object.assign(PROFILE, ' + JSON.stringify(test.patch) + ')');
    const result = await new AsyncFunction('args','agent','parallel','phase','log', body)(
      test.args, agent, async thunks => Promise.all(thunks.map(fn=>fn())), ()=>{}, s=>logs.push(s));
    output.push({calls, logs, result});
  }
  console.log(JSON.stringify(output));
})().catch(e=>{console.error(e); process.exit(1)});
"""


def run_workflows(cases):
    if not shutil.which("node"):
        raise AssertionError("node is required for workflow contract tests")
    result = subprocess.run(["node", "-e", NODE, str(WORKFLOW)], input=json.dumps(cases),
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


# grok is DROPPED without a valid run token (a failed pin must never become
# "latest"), so every fixture that expects grok voices has to carry one.
GROK_MODEL = "grok-4.7"
GROK_TOKEN = ("selected=grok-4.7;latest_candidate=grok-4.7;source=latest;"
              "catalog=ok;cli_version=1.0.40")


def args(**overrides):
    return dict(adapter="/fixture adapter/'$(not-executed).sh", diffFile="/fixture/diff",
                externalPromptFile="/fixture prompt/'$(not-executed)",
                telemetryFile="/fixture telemetry/'$(not-executed)",
                findingNonce="0123456789abcdef", grok=GROK_TOKEN, **overrides)


def command(call):
    return shlex.split(call["prompt"].split("\n\n")[1])


def flag(argv, name):
    return argv[argv.index(name) + 1]


def checksum(text):
    value = 0x811C9DC5
    for byte in text.encode("utf-8"):
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return f"{value:08x}"


class ProfileTests(unittest.TestCase):
    def test_documented_matrix_matches_execution_source(self):
        text = SKILL.read_text(encoding="utf-8")
        table = text.split("<!-- BEGIN SWARM PROFILE TABLE -->")[1].split("<!-- END SWARM PROFILE TABLE -->")[0]
        rows = {}
        for line in table.splitlines():
            if "`" not in line:
                continue
            path, *values = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
            self.assertNotIn(path, rows)
            rows[path] = [json.loads(value) for value in values]
        expected = {"unit"} | {f"stages.{stage}" for stage in STAGES} | {f"externals.{b}" for b in ("codex", "grok", "kimi")}
        self.assertEqual(set(rows), expected)
        for index, name in enumerate(NAMES):
            for path, values in rows.items():
                actual = PROFILES[name]
                for key in path.split("."):
                    actual = actual[key]
                if isinstance(actual, dict):
                    fields = ("model", "effort") if path.startswith("stages.") else ("model", "effort", "tools", "toolBudget")
                    actual = [actual[key] for key in fields]
                self.assertEqual(values[index], actual, f"{name}/{path}")

    def test_all_five_stage_calls_use_profile_options(self):
        cases = [{"args": args(profile=name, externalVoices=["codex", "grok", "kimi"])} for name in NAMES]
        for name, output in zip(NAMES, run_workflows(cases)):
            seen = set()
            for call in output["calls"]:
                opts = call["opts"]
                stage = {"Scope":"gate", "Merge":"merge", "Verify":"verify"}.get(opts["phase"])
                if stage is None:
                    stage = "finder" if opts["label"].startswith("claude:") else "transport"
                seen.add(stage)
                spec = PROFILES[name]["stages"][stage]
                self.assertEqual(opts["effort"], spec["effort"], (name, stage))
                if spec["model"] is None:
                    self.assertNotIn("model", opts, (name, stage))
                else:
                    self.assertEqual(opts["model"], spec["model"], (name, stage))
            self.assertEqual(seen, STAGES)
            self.assertNotIn("opus", json.dumps(output["result"]["balance"]["agents"]))
            self.assertIn(f"Review profile: {name}", output["logs"])

    def test_profile_reporting_preserves_voice_loss_accounting(self):
        # Exercise the complete merged workflow, not only its extracted shapers:
        # profile labels must survive both valid empty reviews and lost voices.
        for name in NAMES:
            unit = "security" if name == "max" else "threat"
            cases = []
            for shape in ("good", "empty", "missing", "malformed"):
                results = {}
                if shape != "good":
                    for backend in ("claude", "codex"):
                        result = None if shape == "missing" else {"findings": [] if shape == "empty" else "invalid"}
                        if backend != "claude" and result is not None:
                            result.update(ok=True, error="")
                        results[f"{backend}:{unit}"] = result
                cases.append({"args": args(profile=name, externalVoices=["codex", "grok"]),
                              "voiceResults": results})
            for shape, output in zip(("good", "empty", "missing", "malformed"), run_workflows(cases)):
                with self.subTest(profile=name, shape=shape):
                    result = output["result"]
                    balance = result["balance"]
                    lost = 2 if shape in ("missing", "malformed") else 0
                    planned = sum(c["opts"]["phase"] == "Fan-out" for c in output["calls"])
                    self.assertEqual(balance["voices"], planned)
                    self.assertEqual(balance["voicesReturned"], planned - lost)
                    self.assertEqual(len(result["backendErrors"]), lost)
                    agents = {a["backend"]: a for a in balance["agents"]}
                    self.assertEqual(agents["claude"]["model"], "session")
                    self.assertEqual(agents["codex"]["model"], PROFILES[name]["externals"]["codex"]["model"])
                    self.assertEqual(sum(a["failedVoices"] for a in agents.values()), lost)
                    self.assertEqual(bool(balance["coverageNotes"]), bool(lost))
                    if lost:
                        self.assertIn(f"{planned - lost} von {planned} Stimmen", balance["coverageNotes"][0])
                        expected = "no diagnostic" if shape == "missing" else "schema-invalid"
                        self.assertTrue(all(expected in e["error"] for e in result["backendErrors"]))

    def test_explicit_stage_models_are_not_discarded(self):
        stages = {stage: {"model": f"fixture-{stage}", "effort": "low"} for stage in STAGES}
        output = run_workflows([{"args": args(), "patch": {"stages": stages}}])[0]
        self.assertEqual({c["opts"].get("model") for c in output["calls"]},
                         {f"fixture-{stage}" for stage in STAGES})

    def test_strict_input_and_legacy_max_cannot_escalate(self):
        invalid = [None, True, False, 1, 0, [], ["max"], {}, {"max": True}, "MAX", " max", "max ", "__proto__", "constructor", "true", ""]
        cases = [{"args": args(profile=value, max=True)} for value in invalid]
        cases += [{"args": args(max=True)}, {"args": json.dumps(args(max=True))}]
        for value in invalid:
            self.assertEqual(profile_name(value), "default")
        for output in run_workflows(cases):
            self.assertIn("Review profile: default", output["logs"])
            for call in output["calls"]:
                if call["opts"]["label"].startswith("codex:"):
                    self.assertEqual(flag(command(call), "--model"), PROFILES["default"]["externals"]["codex"]["model"])

    def test_external_policies_quoting_checksum_and_opt_in(self):
        cases = [{"args": args(profile=name, claude=False, externalVoices=["codex", "grok", "kimi"])} for name in NAMES]
        results = run_workflows(cases)
        for name, output in zip(NAMES, results):
            units = {b: [] for b in ("codex", "grok", "kimi")}
            for call in output["calls"]:
                self.assertNotEqual(call["opts"]["phase"], "Scope")
                self.assertFalse(call["opts"]["label"].startswith("claude:"))
                if call["opts"]["phase"] != "Fan-out":
                    continue
                backend, unit = call["opts"]["label"].split(":")
                units[backend].append(unit)
                spec = PROFILES[name]["externals"][backend]
                argv = command(call)
                # A null profile model means "not pinned by the profile". For grok
                # the run's frozen id fills it, so every voice still gets ONE
                # explicit --model; only a truly unpinned backend omits the flag.
                expected = GROK_MODEL if backend == "grok" else spec["model"]
                if expected is None:
                    self.assertNotIn("--model", argv)
                else:
                    self.assertEqual(flag(argv, "--model"), expected)
                self.assertEqual(flag(argv, "--effort"), spec["effort"])
                self.assertEqual(flag(argv, "--tools"), str(spec["tools"]).lower())
                self.assertEqual(flag(argv, "--tool-budget"), str(spec["toolBudget"]))
                self.assertIn(args()["adapter"], argv)
                self.assertEqual(flag(argv, "--prompt-file"), args()["externalPromptFile"])
                self.assertEqual(flag(argv, "--telemetry"), args()["telemetryFile"])
                self.assertEqual(flag(argv, "--unit"), unit)
                instruction = flag(argv, "--lens-instr")
                self.assertEqual(flag(argv, "--lens-instr-sum"), checksum(instruction))
                self.assertNotIn("\n", instruction)
                self.assertIn("Review ONLY through these lens(es)", instruction)
                # The adapter appends tool policy from flags, outside this scope checksum.
                self.assertEqual("SWARM_KIMI=1" in argv, backend == "kimi")
            self.assertEqual(len(units["codex"]), 11 if name == "max" else 5)
            self.assertEqual(units["codex"], units["grok"])
            self.assertEqual(set(units["kimi"]), {"correctness", "removed-behavior", "security", "adversarial"} if name == "max" else {"breakage", "threat"})
        for output in run_workflows([{"args": args(profile=name, claude=False)} for name in NAMES]):
            self.assertFalse(any(c["opts"]["label"].startswith("kimi:") for c in output["calls"]))

    def test_backend_budgets_never_share_a_unit_cache(self):
        externals = copy.deepcopy(PROFILES["quick"]["externals"])
        externals["codex"]["toolBudget"] = 3
        externals["grok"]["toolBudget"] = 7
        output = run_workflows([{"args": args(profile="quick", externalVoices=list(externals)), "patch": {"externals": externals}}])[0]
        for call in output["calls"]:
            backend = call["opts"]["label"].split(":")[0]
            if backend not in externals:
                continue
            argv = command(call)
            instruction = flag(argv, "--lens-instr")
            budget = externals[backend]["toolBudget"]
            self.assertEqual(flag(argv, "--tool-budget"), str(budget))
            self.assertEqual(flag(argv, "--tools"), str(externals[backend]["tools"]).lower())
            self.assertEqual(flag(argv, "--lens-instr-sum"), checksum(instruction))

    def test_model_id_grammar_agrees_across_layers(self):
        # One model id crosses four validators: the staged-map accessor, the
        # workflow's externalFlags, the adapter's --model check and its Codex
        # catalog parser. They drifted apart once ('+' allowed only in the
        # adapter), so an id could pass one layer and be refused by the next.
        import profiles
        adapter = (HERE / "agents.sh").read_text(encoding="utf-8")
        found = {
            "profiles.py": profiles.MODEL.pattern.removesuffix(r"\Z"),
            "workflow": re.search(r"/\^(\[A-Za-z0-9\]\[[^\]]*\]\*)\$/\.test\(b\.model\)", SOURCE).group(1),
            "adapter validate_model": re.search(r'validate_model\(\) \{\n\s*\[\[ "\$1" =~ \^(\S+)\$ \]\]', adapter).group(1),
            "adapter catalog": re.search(r're\.fullmatch\(r"(\[A-Za-z0-9\]\[[^"]*)", name\)', adapter).group(1),
        }
        samples = ["gpt-6-astra", "gpt-5.6-sol", "kimi-code/k3-256k", "grok-4.7", "a+b", "x.y:z_1",
                   "-x", "+x", "a b", "a$b", "a;b", "a`b", "a'b", "a|b", ""]
        verdicts = {name: [bool(re.fullmatch(pattern, sample)) for sample in samples]
                    for name, pattern in found.items()}
        reference = verdicts["profiles.py"]
        for name, verdict in verdicts.items():
            self.assertEqual(verdict, reference, f"{name} disagrees with profiles.py on {samples}")
        self.assertTrue(all(reference[:6]) and not any(reference[6:]))

    def test_readme_matrix_matches_profiles(self):
        # The README restates the matrix for readers; nothing else pinned it, so
        # a profile edit could leave the user-facing table silently wrong.
        text = (PLUGIN / "README.md").read_text(encoding="utf-8")
        section = text.split("## Review profiles", 1)[1]
        lines = section.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("| Setting |"))
        table = []
        for line in lines[start + 2:]:
            if not line.startswith("|"):
                break
            table.append(line)
        rows = {}
        for line in table:
            label, *cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            rows[label] = cells
        def pair(spec, null_label):
            return f"{spec['model'] if spec['model'] is not None else null_label} / {spec['effort']}"
        expect = {
            "Gate": lambda p: pair(p["stages"]["gate"], "session model"),
            "Finders / verify": lambda p: pair(p["stages"]["finder"], "session model"),
            "Merge": lambda p: pair(p["stages"]["merge"], "session model"),
            "Transport wrappers": lambda p: pair(p["stages"]["transport"], "session model"),
            "Codex": lambda p: pair(p["externals"]["codex"], "default"),
            "Grok": lambda p: pair(p["externals"]["grok"], "discovered"),
            "Kimi (opt-in)": lambda p: pair(p["externals"]["kimi"], "default"),
            "Fan-out unit": lambda p: p["unit"],
            "Kimi tools / budget": lambda p: f"{str(p['externals']['kimi']['tools']).lower()} / {p['externals']['kimi']['toolBudget']}",
        }
        self.assertEqual(set(rows), set(expect))
        for index, name in enumerate(NAMES):
            profile = PROFILES[name]
            self.assertEqual(profile["stages"]["finder"], profile["stages"]["verify"],
                             "README folds finders and verify into one row")
            for label, render in expect.items():
                self.assertEqual(rows[label][index], render(profile), f"README {label} / {name}")

    def test_staged_accessor_and_fail_closed_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "staged.js"
            data = copy.deepcopy(PROFILES)
            data["max"]["externals"]["codex"]["model"] = "fixture-selected-model"
            def write(value):
                staged.write_text(BEGIN + "const PROFILES = " + json.dumps(value) + "\n" + END, encoding="utf-8")
            write(data)
            proc = subprocess.run([sys.executable, str(HERE / "profiles.py"), "--workflow", str(staged), "--profile", "max", "--codex-model"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), "fixture-selected-model")
            mutations = [("model", "$(touch nope)"), ("model", None), ("effort", "max"), ("tools", False), ("toolBudget", True), ("toolBudget", -1), ("toolBudget", 0)]
            for key, value in mutations:
                changed = copy.deepcopy(data)
                changed["max"]["externals"]["codex"][key] = value
                write(changed)
                with self.assertRaises(ValueError, msg=f"{key}={value}"):
                    load_profiles(staged)
            for source in (BEGIN + 'const PROFILES = {"quick":{},"quick":{}}\n' + END,
                           BEGIN + 'const PROFILES = (()=>({}))()\n' + END,
                           SOURCE + "\n" + BEGIN + "const PROFILES = {}\n" + END):
                staged.write_text(source, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_profiles(staged)

    def test_preparation_resolves_the_staged_map_for_each_profile(self):
        skill = SKILL.read_text(encoding="utf-8")
        prep = re.search(r"^```sh\n(.*?)^```", skill, re.M | re.S).group(1)
        # The profile-resolution prefix is real shell + the real accessor; stop
        # before diff/probes. Everything it creates is under this private temp dir.
        prefix = prep.split("# --- Diff source:", 1)[0].replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN))
        for name in NAMES:
            with self.subTest(profile=name), tempfile.TemporaryDirectory() as tmp:
                env = {key:value for key, value in os.environ.items()
                       if not key.startswith("SWARM_") and key not in ("REVIEW_PR", "FIX_OR_LOOP")}
                env.update(TMPDIR=tmp, PWD=tmp)
                env["SWARM_QUICK"] = "1" if name == "quick" else "0"
                env["SWARM_MAX"] = "1" if name == "max" else "0"
                proc = subprocess.run(["/bin/bash", "-c", prefix], cwd=tmp, env=env,
                                      capture_output=True, text=True, timeout=10)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(f"PROFILE={name}\n", proc.stdout)
                self.assertIn(f"CODEX_MODEL={PROFILES[name]['externals']['codex']['model']}\n", proc.stdout)
                staged = list(Path(tmp).glob(".swarm-workflow.*/swarm-review.js"))
                self.assertEqual(len(staged), 1)
                self.assertEqual(load_profiles(staged[0]), PROFILES)

    def test_skill_guards_precede_side_effects_and_preserve_profile(self):
        skill = SKILL.read_text(encoding="utf-8")
        prep = re.search(r"^```sh\n(.*?)^```", skill, re.M | re.S).group(1)
        with tempfile.TemporaryDirectory() as tmp:
            env = {key:value for key, value in os.environ.items() if not key.startswith("SWARM_")}
            env.update(TMPDIR=tmp, PATH="/nonexistent")
            for prefix, token in (("SWARM_QUICK=1; SWARM_MAX=1;", "SWARM_PROFILE_ERR"),
                                  ("REVIEW_PR=1; FIX_OR_LOOP=1;", "SWARM_PR_ERR")):
                proc = subprocess.run(["/bin/bash", "-c", prefix + prep], cwd=tmp, env=env, capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(proc.stdout.startswith(token), proc.stdout)
                self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertIn('--workflow "$WORKFLOW" --profile "$PROFILE" --codex-model', prep)
        self.assertIn('list --json --codex-model "$CODEX_MODEL"', prep)
        self.assertLess(prep.index('scripts/profiles.py'), prep.index('list --json --codex-model'))
        self.assertNotIn("gpt-", prep, "no second shell model table")
        self.assertIn('profile: "<PROFILE>"', skill)
        self.assertNotIn("max: true", skill)
        self.assertIn("including rows with `ready=true`", skill)
        self.assertIn("Strip both before interpreting the remaining pathspec/ref", skill)
        self.assertIn("Preserve the selected profile", skill)
        self.assertIn("Kimi opt-in (`--kimi`/`SWARM_KIMI`)", skill)


if __name__ == "__main__":
    unittest.main()
