from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, Request, UploadFile
from fastapi.responses import JSONResponse

from .adapters.airtable import AirtableGateway
from .adapters.elevenlabs import ElevenLabsVoice
from .adapters.vision import build_vision
from .config import Settings
from .models import (AssetLookupRequest, AssessRequest, BindRequest, ConfirmActionRequest, DomainError,
                     FieldObservationRequest,
                     OperatorResponseRequest, PrepareActionRequest, RevisionRequest,
                     SessionRequest, StartInspection)
from .service import Inspections
from .storage import Store


def make_app(settings=None, voice=None, vision=None, store=None, business=None, external=None):
    settings = settings or Settings.from_env()
    store = store or Store(settings.data_dir)
    voice = voice or ElevenLabsVoice(settings.elevenlabs_api_key, settings.agent_id)
    vision = vision or build_vision(settings)
    external = external or AirtableGateway(settings.airtable_token, settings.airtable_base_id,
                                           settings.airtable_table_name)
    service = Inspections(settings, store, voice, vision, business=business, external=external)

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = FastAPI(title="LumaField API", version="0.1.0", lifespan=lifespan)
    app.state.service = service

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status, content={
            "code": exc.code, "message": exc.message, "retryable": exc.retryable,
            "request_id": "server", "current_revision": exc.current_revision,
        })

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/v1/webhooks/elevenlabs/post-call")
    async def receive_elevenlabs_post_call(
        request: Request,
        elevenlabs_signature: Annotated[
            str | None, Header(alias="ElevenLabs-Signature")
        ] = None,
    ):
        return service.receive_post_call(await request.body(), elevenlabs_signature)

    def browser_session(authorization: Annotated[str | None, Header()] = None):
        return service.authenticate_browser(authorization)

    def tool_session(
        x_agent_key: Annotated[str | None, Header(alias="X-Agent-Key")] = None,
        x_tool_session: Annotated[str | None, Header(alias="X-Tool-Session")] = None,
        x_conversation_id: Annotated[str | None, Header(alias="X-Conversation-Id")] = None,
    ):
        return service.authenticate_tool(x_agent_key, x_tool_session, x_conversation_id)

    def any_session(
        x_agent_key: Annotated[str | None, Header(alias="X-Agent-Key")] = None,
        x_tool_session: Annotated[str | None, Header(alias="X-Tool-Session")] = None,
        x_conversation_id: Annotated[str | None, Header(alias="X-Conversation-Id")] = None,
        authorization: Annotated[str | None, Header()] = None,
    ):
        # The inspection read serves both callers: the browser console
        # (Bearer capability) and the agent's get_inspection_state tool
        # (X-Agent-Key trio). Tool headers win when present.
        if x_agent_key or x_tool_session:
            return service.authenticate_tool(x_agent_key, x_tool_session, x_conversation_id)
        return service.authenticate_browser(authorization)

    @app.post("/v1/sessions", status_code=201)
    def create_session(
        payload: SessionRequest,
        x_demo_access: Annotated[str | None, Header(alias="X-Demo-Access")] = None,
        authorization: Annotated[str | None, Header()] = None,
    ):
        # X-Demo-Access is the documented interface. Authorization remains an
        # accepted local-development alias so existing console sessions survive.
        credential = ("Bearer " + x_demo_access) if x_demo_access else authorization
        return service.create_session(payload, credential)

    @app.post("/v1/sessions/{session_id}/bind")
    def bind_conversation(session_id: str, payload: BindRequest, session=Depends(browser_session)):
        return service.bind(session, session_id, payload.conversation_id)

    @app.get("/v1/sessions/{session_id}")
    def get_session(session_id: str, session=Depends(browser_session)):
        return service.get_session(session, session_id)

    @app.post("/v1/sessions/{session_id}/voice-credential")
    def create_voice_credential(session_id: str, session=Depends(browser_session)):
        return service.create_voice_credential(session, session_id)

    @app.post("/v1/inspections/start")
    def start_inspection(payload: StartInspection, session=Depends(tool_session)):
        return service.start_inspection(session, payload)

    @app.get("/v1/assets")
    def list_assets(session=Depends(browser_session)):
        return service.list_assets(session)

    @app.get("/v1/assets/{pole_id}")
    def get_asset_record(pole_id: str, session=Depends(any_session)):
        return service.get_asset_record(session, pole_id)

    @app.post("/v1/asset-records/lookup")
    def lookup_asset_record(payload: AssetLookupRequest, session=Depends(tool_session)):
        return service.get_asset_record(session, payload.pole_id)

    @app.get("/v1/analytics/inspections")
    def inspection_summary(session=Depends(any_session)):
        return service.inspection_summary(session)

    @app.get("/v1/inspections")
    def inspection_history(session=Depends(browser_session)):
        return service.inspection_history(session)

    @app.post("/v1/inspections/{inspection_id}/evidence")
    async def upload_evidence(
        inspection_id: str,
        file: Annotated[UploadFile, File(description="JPEG or PNG image; normalized server-side")],
        expected_revision: int,
        session=Depends(browser_session),
    ):
        # Do not trust UploadFile.filename or content_type. media.normalize_image decodes pixels.
        raw = await file.read(10 * 1024 * 1024 + 1)
        return service.upload(session, inspection_id, expected_revision, raw)

    @app.post("/v1/inspections/{inspection_id}/field-observation")
    def record_field_observation(
        inspection_id: str,
        payload: FieldObservationRequest,
        session=Depends(tool_session),
    ):
        return service.record_field_observation(session, inspection_id, payload)

    @app.post("/v1/inspections/{inspection_id}/assessments")
    def assess_evidence(inspection_id: str, payload: AssessRequest, session=Depends(tool_session)):
        return service.assess(session, inspection_id, payload)

    @app.post("/v1/inspections/{inspection_id}/reconcile")
    def reconcile_inspection(inspection_id: str, payload: RevisionRequest, session=Depends(tool_session)):
        return service.reconcile(session, inspection_id, payload.expected_revision)

    @app.post("/v1/inspections/{inspection_id}/operator-responses")
    def record_operator_response(inspection_id: str, payload: OperatorResponseRequest, session=Depends(tool_session)):
        return service.record_operator_response(session, inspection_id, payload)

    @app.post("/v1/inspections/{inspection_id}/proposals", status_code=201)
    def prepare_action(inspection_id: str, payload: PrepareActionRequest, session=Depends(tool_session)):
        return service.prepare_action(session, inspection_id, payload)

    @app.post("/v1/proposals/{proposal_id}/confirmation", status_code=202)
    def confirm_action(proposal_id: str, payload: ConfirmActionRequest, session=Depends(tool_session)):
        return service.confirm_action(session, proposal_id, payload)

    @app.get("/v1/operations/{operation_id}")
    def get_operation_status(operation_id: str, session=Depends(tool_session)):
        return service.get_operation_status(session, operation_id)

    @app.get("/v1/inspections/{inspection_id}")
    def get_inspection(inspection_id: str, session=Depends(any_session)):
        return service.get_inspection(session, inspection_id)

    return app


app = make_app()
