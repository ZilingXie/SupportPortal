"""Evidence carrier must detect intentionally broken inputs and raw replies."""
from scripts.testing import natural_persona_eval as evaluation
from backend.services.prompt_runtime import initialize_prompt_runtime


def test_negative_controls_detect_all_contract_failures():
    initialize_prompt_runtime()
    controls = evaluation.negative_controls()
    assert len(controls) == 7
    assert all(item["detected"] for item in controls), controls


def test_nine_samples_use_consistent_canonical_context_without_expectations():
    initialize_prompt_runtime()
    assert len(evaluation.SAMPLES) == 9
    for sample in evaluation.SAMPLES:
        instructions, _ = evaluation._system_prompt(sample["basis"]["direction"], sample["route"])
        evaluation.validate_carrier(instructions, evaluation._snapshot(sample), sample["basis"])
        request = evaluation._user_prompt(sample)
        assert "expect_terms" not in request
        assert "seed" not in request
    assert "Jordan" in evaluation._user_prompt(evaluation.SAMPLES[7])


def test_raw_duplicate_greeting_is_rejected_before_projection():
    verdict, projected = evaluation.judge_draft("Hi Customer,\nHi Customer,\nPlease share your App ID.", evaluation.SAMPLES[0]["basis"])
    assert verdict == {"passed": False, "code": "duplicate_greeting"}
    assert projected is None
