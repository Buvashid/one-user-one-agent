import json
import os
import unittest
from urllib.error import HTTPError, URLError

from src.model_provider import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    OpenAICompatibleProvider,
    ProviderConfig,
    ProviderError,
    run_contract_probe,
    validate_contract_output,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def provider_with(opener):
    return OpenAICompatibleProvider(
        ProviderConfig('real', 'test-only-key', DEFAULT_BASE_URL, DEFAULT_MODEL, 1),
        opener=opener,
    )


class ProviderUnitTests(unittest.TestCase):
    def test_successful_response_is_parsed_and_validated(self):
        body = json.dumps({
            'choices': [{
                'message': {
                    'content': json.dumps({
                        'decision': 'abstain',
                        'confidence': 0,
                        'claims': [{'name': 'data_scope', 'value': 'synthetic_only'}],
                    })
                }
            }]
        }).encode()
        result = run_contract_probe(provider_with(lambda req, timeout: FakeResponse(body)))
        self.assertEqual(result['decision'], 'abstain')

    def test_malformed_json_and_missing_content_fail_closed(self):
        bad = provider_with(lambda req, timeout: FakeResponse(b'{'))
        with self.assertRaisesRegex(ProviderError, 'valid JSON'):
            bad.complete_json('probe')
        missing = provider_with(lambda req, timeout: FakeResponse(b'{}'))
        with self.assertRaisesRegex(ProviderError, 'model content'):
            missing.complete_json('probe')

    def test_http_auth_failure_is_sanitized(self):
        def fail(req, timeout):
            raise HTTPError(req.full_url, 401, 'unauthorized', {}, None)
        with self.assertRaisesRegex(ProviderError, 'HTTP error 401') as raised:
            provider_with(fail).complete_json('probe')
        self.assertNotIn('test-only-key', str(raised.exception))

    def test_request_uses_configured_auth_endpoint_timeout_and_synthetic_payload(self):
        captured = {}
        endpoint = 'https://example.test/v1/chat/completions'
        config = ProviderConfig('real', 'test-only-key', endpoint, 'test-model', 3.5)

        def capture(req, timeout):
            captured['url'] = req.full_url
            captured['authorization'] = req.get_header('Authorization')
            captured['timeout'] = timeout
            captured['payload'] = json.loads(req.data.decode('utf-8'))
            return FakeResponse(json.dumps({
                'choices': [{
                    'message': {
                        'content': json.dumps({
                            'decision': 'abstain',
                            'confidence': 0,
                            'claims': [],
                        })
                    }
                }]
            }).encode())

        result = run_contract_probe(OpenAICompatibleProvider(config, opener=capture))
        self.assertEqual(result['decision'], 'abstain')
        self.assertEqual(captured['url'], endpoint)
        self.assertEqual(captured['authorization'], 'Bearer ' + 'test-only-key')
        self.assertEqual(captured['timeout'], 3.5)
        self.assertEqual(captured['payload']['model'], 'test-model')
        prompt = captured['payload']['messages'][1]['content']
        self.assertIn('synthetic provider contract probe', prompt)
        self.assertNotIn('one_user_one_agent_pack', prompt)
        self.assertNotIn('replay', prompt)
        self.assertNotIn('user_id', prompt)
        self.assertNotIn('session_id', prompt)

    def test_http_failure_timeout_and_invalid_content_fail_closed(self):
        with self.assertRaisesRegex(ProviderError, 'request failed'):
            provider_with(lambda req, timeout: (_ for _ in ()).throw(TimeoutError())).complete_json('probe')
        with self.assertRaisesRegex(ProviderError, 'request failed'):
            provider_with(lambda req, timeout: (_ for _ in ()).throw(URLError('offline'))).complete_json('probe')
        content = json.dumps({
            'choices': [{'message': {'content': 'not-json'}}]
        }).encode()
        with self.assertRaisesRegex(ProviderError, 'content was not valid JSON'):
            provider_with(lambda req, timeout: FakeResponse(content)).complete_json('probe')

    def test_invalid_schema_and_unsupported_claims_are_rejected(self):
        valid = {
            'decision': 'abstain',
            'confidence': 0,
            'claims': [],
        }
        self.assertIsNone(validate_contract_output(valid))
        self.assertIsNotNone(validate_contract_output(dict(valid, extra=True)))
        self.assertIsNotNone(validate_contract_output(dict(valid, decision='publish')))
        self.assertIsNotNone(validate_contract_output({
            **valid,
            'claims': [{'name': 'data_scope', 'value': 'private_pack'}],
        }))

    def test_real_mode_requires_key_and_mock_is_default(self):
        old = os.environ.copy()
        try:
            os.environ.pop('MODEL_MODE', None)
            os.environ.pop('OPENAI_API_KEY', None)
            self.assertEqual(ProviderConfig.from_env().mode, 'mock')
            os.environ['MODEL_MODE'] = 'real'
            with self.assertRaisesRegex(ProviderError, 'OPENAI_API_KEY'):
                ProviderConfig.from_env()
        finally:
            os.environ.clear()
            os.environ.update(old)


@unittest.skipUnless(
    os.environ.get('MODEL_MODE', '').lower() == 'real'
    and bool(os.environ.get('OPENAI_API_KEY')),
    'opt-in live provider test; requires MODEL_MODE=real and OPENAI_API_KEY',
)
class RealProviderSmokeTests(unittest.TestCase):
    def test_live_synthetic_contract_probe(self):
        result = run_contract_probe(OpenAICompatibleProvider.from_env())
        self.assertEqual(result['decision'], 'abstain')


if __name__ == '__main__':
    unittest.main()
