"""Exercise the real production provider constructor without network calls."""
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


class TimeoutConfiguration(unittest.TestCase):
    def test_driver_configures_the_client_imported_by_production(self):
        here=Path(__file__).resolve()
        if here.parent.name=='setup':
            root=here.parent.parent
            import json
            source=Path(json.loads((root/'setup/study.json').read_text())['source_root'])
            adapter=root/'runtime/adapters/_apexa_driver.py'
        else:
            source=here.parents[2];adapter=source/'benchmark/harness_comparison/_apexa_driver.py'
        sys.path.insert(0,str(source))
        import openai
        import apexa_provider_openai
        spec=importlib.util.spec_from_file_location('timeout_test_driver',adapter)
        driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
        original=openai.AsyncOpenAI
        try:
            with patch.dict(os.environ,{'APEXA_BENCH_RESPONSE_TIMEOUT':'300'}), patch.object(
                    apexa_provider_openai,'api_key_for',return_value='local-test-only'):
                driver.configure_response_timeout()
                provider=apexa_provider_openai.OpenAICompatProvider('local-test','openai/gpt-oss-120b',
                           base_url_override='http://127.0.0.1:9/v1')
                self.assertEqual(provider._client.timeout,300)
                asyncio.run(provider._client.close())
        finally:
            openai.AsyncOpenAI=original


if __name__=='__main__':unittest.main()
