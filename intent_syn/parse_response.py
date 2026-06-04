import json
import traceback
import os

def _parse_response_field(result: dict) -> dict:
    if not isinstance(result, dict):
        return result

    response = result.get("response")

    if isinstance(response, (dict, list)):
        return result

    # 字符串尝试 json.loads
    if isinstance(response, str):
        raw = response.strip()

        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        try:
            result["response"] = json.loads(raw)
        except Exception as e:
            result["response_parse_error"] = str(e)
            result["response_raw"] = response

    return result

with open("/mnt/DataFlow/xc/h200_copy/xc/multi-bench/synthesis_process/outputs_test/intent_results_1.json", "r") as f:
    results = json.load(f)

for result in results:
    result = _parse_response_field(result)

with open("/mnt/DataFlow/xc/h200_copy/xc/multi-bench/synthesis_process/outputs_test/intent_results_1_parsed.json", "w") as f:
    json.dump(results, f, indent=4)
