"""Exercise the real production provider constructor without network calls."""
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


class TimeoutConfiguration(unittest.TestCase):
    def test_driver_passes_explicit_application_contract_to_public_query_api(self):
        import json
        here=Path(__file__).resolve()
        if here.parent.name=='setup':
            root=here.parent.parent
            source=Path(json.loads((root/'setup/study.json').read_text())['source_root'])
            adapter=root/'runtime/adapters/_apexa_driver.py'
        else:
            source=here.parents[2];adapter=source/'benchmark/harness_comparison/_apexa_driver.py'
        sys.path.insert(0,str(source))
        spec=importlib.util.spec_from_file_location('application_test_driver',adapter)
        driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
        supplied={'contract_path':'task.json','image_file':'acquisition.h5','template_param_file':'template.txt'}
        calls=[]
        class Client:
            sessions={'core':object(),'midas':object()}
            async def connect_to_multiple_servers(self,configs):pass
            async def run_query(self,prompt,**kwargs):calls.append(kwargs);return 'completed'
            async def cleanup(self):pass
        with patch.dict(os.environ,{'APEXA_BENCH_CALIBRATION_CONTRACT':json.dumps(supplied),
                'APEXA_PROCEDURE_AUDIT':'','APEXA_BENCH_TOOL_AUDIT':''}), \
                patch.object(driver,'knowledge_preflight'),patch.object(driver,'solver_environment'), \
                patch.object(driver,'configure_response_timeout'),patch.object(driver,'inherit_server_environment'), \
                patch.object(driver,'server_configs',return_value=[]),patch('argo_mcp_client.APEXAClient',Client):
            self.assertEqual(asyncio.run(driver._go('review',10)),'completed')
        self.assertEqual(calls[0]['calibration_contract'],supplied)

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
