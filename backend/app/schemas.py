from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import Winner


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    description: str | None
    evaluation_instructions: str | None


class CategoriesResponse(BaseModel):
    categories: list[CategoryResponse]


class TaskResponse(BaseModel):
    category_code: str
    prompt: str
    response_a: str
    response_b: str
    token: str


class SkipTaskRequest(BaseModel):
    token: str


class SkipTaskResponse(BaseModel):
    status: str = "ok"


class TaskProgressResponse(BaseModel):
    total: int
    voted: int
    skipped: int
    remaining: int


class VoteRequest(BaseModel):
    winner: Winner
    token: str


class VoteResponse(BaseModel):
    status: str = "ok"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    consent: bool


class RegisterResponse(BaseModel):
    status: str = "pending_verification"


class VerifyEmailRequest(BaseModel):
    token: str


class VerifyEmailResponse(BaseModel):
    status: str = "verified"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    status: str = "logged_in"


class SessionResponse(BaseModel):
    """Estat de la sessió. `authenticated` fals no és cap error: vol dir que no n'hi ha."""

    authenticated: bool
    email: str | None = None
    email_verified: bool = False


class LogoutRequest(BaseModel):
    token: str


class LogoutResponse(BaseModel):
    status: str = "logged_out"


class DeleteAccountRequest(BaseModel):
    current_password: str


class DeleteAccountResponse(BaseModel):
    status: str = "deleted"


class ExportVoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    prompt_id: int
    response_a_id: int
    response_b_id: int
    winner: Winner
    session_id: str | None
    response_time_s: float | None
    created_at: datetime


class ExportUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str | None
    email_verified_at: datetime | None
    consent_version: str
    consent_at: datetime | None
    created_at: datetime
    deleted_at: datetime | None


class ExportDataResponse(BaseModel):
    user: ExportUserResponse
    votes: list[ExportVoteResponse]


class PairwiseStat(BaseModel):
    model_a: str
    model_b: str
    wins_a: int
    wins_b: int
    ties: int
    neither: int
    win_rate_a: float | None


class ConfidenceInterval(BaseModel):
    lo: float
    hi: float


class RankingConfidence(BaseModel):
    category_code: str | None
    best_model: str | None
    n_prompts: int
    n_decisive_votes: int
    p_best_is_best: float
    confidence_interval: ConfidenceInterval
    is_stable: bool


class RankedModel(BaseModel):
    rank: int
    model: str
    bt_skill: float


class RankingResponse(BaseModel):
    category_code: str | None
    n_votes_total: int
    n_votes_decisive: int
    n_ties: int
    n_neither: int
    best_model: str | None
    ranked_models: list[RankedModel]
    confidence: RankingConfidence
