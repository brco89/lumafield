from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(min_length=1, max_length=160)]
RequestId = Annotated[str, Field(min_length=8, max_length=160)]
OperatorClass = Literal["LED", "LEGACY", "UNSURE"]
ShortText = Annotated[str, Field(min_length=1, max_length=300)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionRequest(StrictModel):
    request_id: RequestId
    operator_display_name: str | None = Field(default=None, max_length=80)


class BindRequest(StrictModel):
    conversation_id: Id


class AssetLookupRequest(StrictModel):
    pole_id: str = Field(min_length=1, max_length=80)


class StartInspection(StrictModel):
    request_id: RequestId
    pole_id: str = Field(min_length=1, max_length=80)
    operator_classification: OperatorClass | None = None
    operator_utterance: str | None = Field(default=None, min_length=1, max_length=1000)
    fixture_selector: str | None = Field(default=None, max_length=80)
    supersedes_inspection_id: Id | None = None
    expected_previous_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def supersede_pair(self):
        if (self.supersedes_inspection_id is None) != (self.expected_previous_revision is None):
            raise ValueError("Superseding requires both inspection ID and revision")
        if (self.operator_classification is None) != (self.operator_utterance is None):
            raise ValueError("Initial observation requires both classification and utterance")
        return self


class FieldObservationRequest(StrictModel):
    request_id: RequestId
    expected_revision: int = Field(ge=1)
    operator_classification: OperatorClass
    operator_utterance: str = Field(min_length=1, max_length=1000)


class AssessRequest(StrictModel):
    evidence_id: Id
    expected_revision: int = Field(ge=1)


class RevisionRequest(StrictModel):
    expected_revision: int = Field(ge=1)


class OperatorResponseRequest(StrictModel):
    request_id: RequestId
    expected_revision: int = Field(ge=1)
    kind: Literal["ACCEPT_VISUAL", "MAINTAIN_ORIGINAL", "UNSURE", "DECLINE_RETAKE"]
    assessment_id: Id | None = None
    operator_utterance: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def acceptance_requires_assessment(self):
        if self.kind == "ACCEPT_VISUAL" and not self.assessment_id:
            raise ValueError("Accepting visual evidence requires an assessment")
        return self


class PrepareActionRequest(StrictModel):
    request_id: RequestId
    expected_revision: int = Field(ge=1)


class ConfirmActionRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    decision: Literal["CONFIRM", "DECLINE"]
    operator_utterance: str = Field(min_length=1, max_length=1000)


class VisualResult(StrictModel):
    visual_classification: Literal["LED", "LEGACY", "INCONCLUSIVE"]
    visual_confidence: Literal["HIGH", "MEDIUM", "LOW"]
    evidence_quality: Literal["GOOD", "LIMITED", "POOR"]
    evidence_origin: Literal["FIELD_PLAUSIBLE", "ILLUSTRATIVE_OR_STOCK", "UNCERTAIN"]
    color_temperature_cue: Literal["WARM_AMBER", "NEUTRAL_WHITE", "COOL_WHITE", "UNCERTAIN"]
    visible_identifiers: list[ShortText] = Field(max_length=8)
    visual_cues: list[ShortText] = Field(max_length=8)
    limitations: list[ShortText] = Field(max_length=8)
    retake_guidance: str | None = Field(max_length=500)
    observed_power_w: float | None = Field(gt=0)
    power_evidence: str | None = Field(max_length=500)

    @model_validator(mode="after")
    def require_power_evidence(self):
        if self.observed_power_w is not None and not (self.power_evidence or "").strip():
            raise ValueError("Power requires readable visual evidence")
        return self

    @property
    def sufficient(self):
        return (self.visual_classification != "INCONCLUSIVE"
                and self.visual_confidence in {"HIGH", "MEDIUM"}
                and self.evidence_quality in {"GOOD", "LIMITED"}
                and self.evidence_origin == "FIELD_PLAUSIBLE")


class DomainError(Exception):
    def __init__(self, code, message, status=409, retryable=False, current_revision=None):
        self.code, self.message, self.status = code, message, status
        self.retryable, self.current_revision = retryable, current_revision
        super().__init__(message)
