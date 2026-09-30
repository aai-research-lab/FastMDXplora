"""The Agent says what "done" means before the run.

Asked for a quantity to a precision, or to run until something is known,
the Agent writes `simulation.stop_when`: the measures, the error each must
reach, replicas, and a ceiling. The person reads it in the plan before
anything runs, and the code, not the model, judges it after. A rule the
study cannot keep is refused by the same validator that gates every
proposal, so the repair loop sees it and the model can fix it; a rule that
reached a run and was refused there would have cost a setup first.
"""

from __future__ import annotations

import yaml

from fastmdxplora.agent.propose import prompt_for, propose_config

RULE_ALONE = {
    "systems": [{"system": "1UAO"}],
    "simulation": {"duration_ns": 5, "stop_when": {
        "measures": [{"analysis": "rmsd", "standard_error": 0.01}], "max_duration_ns": 50}},
}
WITH_REPLICAS = {**RULE_ALONE, "sweep": {"simulation.random_seed": [1, 2, 3]}}


def _replies(*configs):
    given = iter(yaml.safe_dump(config) for config in configs)
    prompts = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        return next(given)
    return complete, prompts


def test_the_agent_is_told_how_to_write_one():
    prompt = prompt_for("simulate chignolin until its RMSD is known to 0.01 nm")
    assert "simulation.stop_when" in prompt and "max_duration_ns" in prompt
    assert "simulation.random_seed: [1, 2, 3]" in prompt
    # The measures it may name are the analyses that record one mean.
    from fastmdxplora.simulation.stopping import judgeable_analyses

    listed = prompt.split("only these record one mean a rule can judge:", 1)[1]
    assert ", ".join(judgeable_analyses()) in listed.replace("\n", " ")
    assert "rmsf" not in listed.split(".", 1)[0]
    # And to answer from the record afterwards.
    assert "answer from the stopping record in the run status" in prompt


def test_a_rule_without_replicas_is_refused_and_repaired():
    complete, prompts = _replies(RULE_ALONE, WITH_REPLICAS)
    proposal = propose_config("simulate chignolin until its RMSD is known to 0.01 nm",
                              complete)
    assert proposal.config is not None
    assert proposal.config["sweep"] == {"simulation.random_seed": [1, 2, 3]}
    first = proposal.attempts[0].refusal
    assert first.code == "simulation.stopping.no_replicas"
    # The repair prompt carries the reason, trapping included.
    assert "trapped" in prompts[1] and "random_seed" in prompts[1]


def test_a_measure_with_no_mean_is_refused_with_the_ones_that_have_one():
    wrong = {**WITH_REPLICAS, "simulation": {"duration_ns": 5, "stop_when": {
        "measures": [{"analysis": "rmsf", "standard_error": 0.01}], "max_duration_ns": 50}}}
    complete, prompts = _replies(wrong, WITH_REPLICAS)
    proposal = propose_config("run until the flexibility is known", complete)
    assert proposal.config is not None
    assert proposal.attempts[0].refusal.code == "config.option.not_permitted"
    assert "rmsd" in prompts[1]


def test_the_plan_shows_the_rule_it_wrote():
    from fastmdxplora.gui.plan import plan_of

    lines = {line["label"]: line["value"] for line in plan_of(WITH_REPLICAS)}
    assert lines["Stops when"] == ("rmsd to ±0.01 nm is determined and the replicas agree; "
                                   "or at 50 ns of production")
    assert lines["Replicas"].startswith("3,")
