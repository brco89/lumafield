import httpx
from ..models import DomainError


class ElevenLabsVoice:
    def __init__(self, api_key: str, agent_id: str, transport=None):
        self.api_key, self.agent_id, self.transport = api_key, agent_id, transport

    def conversation_token(self) -> str:
        if not self.api_key or not self.agent_id:
            raise DomainError("VOICE_NOT_CONFIGURED", "Configure the ElevenLabs agent and credential on the server.", 503)
        try:
            with httpx.Client(timeout=15, transport=self.transport) as client:
                response = client.get(
                    "https://api.elevenlabs.io/v1/convai/conversation/token",
                    params={"agent_id": self.agent_id}, headers={"xi-api-key": self.api_key},
                )
                response.raise_for_status()
                token = response.json()["token"]
                if not isinstance(token, str) or not token:
                    raise ValueError("Missing token")
                return token
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise DomainError("VOICE_UNAVAILABLE", "The voice session could not be opened.", 503, True) from None
