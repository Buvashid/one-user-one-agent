"""Small OpenAI-compatible provider adapter for an isolated synthetic probe."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional
from urllib import error, request


DEFAULT_BASE_URL = 'https://api.openai.com/v1/chat/completions'
DEFAULT_MODEL = 'gpt-4o-mini'
DEFAULT_TIMEOUT_S = 10
PROBE_CONTEXT = {'data_scope': 'synthetic_only', 'decision': 'abstain'}


class ProviderError(RuntimeError):
    """Sanitized provider or contract-validation failure."""


@dataclass(frozen=True)
class ProviderConfig:
    mode: str
    api_key: Optional[str]
    base_url: str
    model: str
    timeout_s: float

    @classmethod
    def from_env(cls):
        mode = os.environ.get('MODEL_MODE', 'mock').strip().lower()
        if mode not in ('mock', 'real'):
            raise ProviderError('MODEL_MODE must be mock or real')
        api_key = os.environ.get('OPENAI_API_KEY')
        if mode == 'real' and not api_key:
            raise ProviderError('OPENAI_API_KEY is required when MODEL_MODE=real')
        try:
            timeout_s = float(os.environ.get('MODEL_TIMEOUT_S', DEFAULT_TIMEOUT_S))
        except ValueError as exc:
            raise ProviderError('MODEL_TIMEOUT_S must be numeric') from exc
        if timeout_s <= 0:
            raise ProviderError('MODEL_TIMEOUT_S must be positive')
        return cls(
            mode=mode,
            api_key=api_key,
            base_url=os.environ.get('MODEL_BASE_URL', DEFAULT_BASE_URL),
            model=os.environ.get('MODEL_NAME', DEFAULT_MODEL),
            timeout_s=timeout_s,
        )


def validate_contract_output(output, context=PROBE_CONTEXT):
    """Return an error string for a provider probe response, or None if valid."""
    if not isinstance(output, dict):
        return 'provider output must be an object'
    if set(output) != {'decision', 'confidence', 'claims'}:
        return 'provider output schema is invalid'
    if output['decision'] != context['decision']:
        return 'provider decision is not the required safe decision'
    confidence = output['confidence']
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        return 'provider confidence is invalid'
    claims = output['claims']
    if not isinstance(claims, list):
        return 'provider claims must be a list'
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {'name', 'value'}:
            return 'provider claim schema is invalid'
        if not isinstance(claim['name'], str) or not isinstance(claim['value'], str):
            return 'provider claim types are invalid'
        if context.get(claim['name']) != claim['value']:
            return 'provider claim is unsupported by trusted synthetic context'
    return None


class OpenAICompatibleProvider:
    def __init__(self, config, opener=None):
        if config.mode != 'real':
            raise ProviderError('real provider requires MODEL_MODE=real')
        if not config.api_key:
            raise ProviderError('OPENAI_API_KEY is required when MODEL_MODE=real')
        self.config = config
        self._opener = opener or request.urlopen

    @classmethod
    def from_env(cls):
        return cls(ProviderConfig.from_env())

    def complete_json(self, prompt):
        body = {
            'model': self.config.model,
            'temperature': 0,
            'messages': [
                {'role': 'system', 'content': 'Return only the requested JSON object.'},
                {'role': 'user', 'content': prompt},
            ],
        }
        req = request.Request(
            self.config.base_url,
            data=json.dumps(body).encode('utf-8'),
            headers={
                'Authorization': 'Bearer ' + self.config.api_key,
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        try:
            with self._opener(req, timeout=self.config.timeout_s) as response:
                payload = json.loads(response.read().decode('utf-8'))
        except error.HTTPError as exc:
            raise ProviderError(f'provider HTTP error {exc.code}') from None
        except (error.URLError, TimeoutError, OSError):
            raise ProviderError('provider request failed or timed out') from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError('provider response was not valid JSON') from None

        try:
            content = payload['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError):
            raise ProviderError('provider response did not contain model content') from None
        if not isinstance(content, str):
            raise ProviderError('provider model content was not text')
        try:
            output = json.loads(content)
        except json.JSONDecodeError:
            raise ProviderError('provider model content was not valid JSON') from None
        validation_error = validate_contract_output(output)
        if validation_error:
            raise ProviderError(validation_error)
        return output


def run_contract_probe(provider):
    prompt = (
        'This is a synthetic provider contract probe. Return exactly one JSON object '
        'with keys decision, confidence, and claims. The only allowed decision is '
        '"abstain". Claims may only repeat trusted synthetic context: '
        '{"data_scope":"synthetic_only","decision":"abstain"}. '
        'Do not request or infer any user, event, catalogue, or private data.'
    )
    return provider.complete_json(prompt)
