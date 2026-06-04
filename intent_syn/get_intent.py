from openai import OpenAI
import json
import requests
import sqlite3
import os
import concurrent.futures
import traceback
import random
from tqdm import tqdm
import tiktoken
from M_Schema.schema_engine import SchemaEngine
from sqlalchemy import event, create_engine
from sqlalchemy.types import INTEGER
from pathlib import Path
from typing import Dict, List
from intent_syn.intent_prompt import PROMPT
from config import API_URL, API_KEY

api_url = API_URL
api_key = API_KEY

# def openai_chat(messages, model='gpt-5.2', temperature=0.6, max_retry=3):
#     headers = {
#         'Authorization': f"Bearer {api_key}",
#         'Content-Type': 'application/json',
#         'User-Agent': 'Apifox/1.0.0 (https://apifox.com)'
#     }
    
#     for attempt in range(max_retry):
#         try:
#             payload = json.dumps({
#                 "model": model,
#                 "messages": [
#                     {"role": "system", "content": "You are a good assistant."},
#                     {"role": "user", "content": messages}
#                 ],
#                 "temperature": temperature
#             })
#             response = requests.post(api_url, headers=headers, data=payload)
            
#             if response.status_code == 200:
#                 return response.json()['choices'][0]['message']['content']
#             else:
#                 print(f"API返回非200状态码: {response.status_code}, 尝试 {attempt + 1}/{max_retry}")
                
#         except requests.exceptions.Timeout:
#             print(f"API超时 (尝试 {attempt + 1}/{max_retry})")
#         except Exception as e:
#             print(f"API错误: {type(e).__name__} (尝试 {attempt + 1}/{max_retry})")
    
#     return None

def openai_chat(messages, model='gpt-5.5', n=3, max_retry=3):
    headers = {
        'Authorization': f"Bearer {api_key}",
        'Content-Type': 'application/json',
        'User-Agent': 'Apifox/1.0.0 (https://apifox.com)'
    }

    for attempt in range(max_retry):
        try:
            payload = json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a good assistant."},
                    {"role": "user", "content": messages}
                ],
                "temperature": 0.6,
                "n": n,
                "stream": True
            })
            response = requests.post(api_url, headers=headers, data=payload)
            response.encoding = 'utf-8'

            if response.status_code == 200:
                buffers = {}
                for line in response.text.splitlines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        for choice in chunk.get("choices", []):
                            idx = choice.get("index", 0)
                            delta = choice.get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                buffers[idx] = buffers.get(idx, "") + content
                    except json.JSONDecodeError:
                        continue
                answers = [buffers[i] for i in sorted(buffers)]
                return answers
            else:
                print(f"API返回非200状态码: {response.status_code}, 尝试 {attempt + 1}/{max_retry}")
                print("响应头:", dict(response.headers))
                print("响应内容:", response.text)

        except requests.exceptions.Timeout:
            print(f"API超时 (尝试 {attempt + 1}/{max_retry})")
        except Exception as e:
            print(f"API错误: {type(e).__name__}: {e} (尝试 {attempt + 1}/{max_retry})")
            if 'response' in locals() and hasattr(response, 'text'):
                print(f"响应内容: {response.text[:500]}")

    return None

insert_stmts_template = '''Below are several `INSERT INTO` statements showing sample values:

{insert_statements}
'''

def get_encoder(model: str = 'gpt-4o'):
    """获取 tiktoken 编码器，按模型选择最合适的编码。"""
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        # 如果模型不被识别，就退回到通用 o200k_base
        enc = tiktoken.get_encoding("o200k_base")
    return enc

def obtain_insert_statements(db_file_dir, table_names, row_num, max_stmt_tokens=1024, model="gpt-4o"):
    table_name2insert_statements = dict()
    conn = sqlite3.connect(db_file_dir)
    cursor = conn.cursor()

    encoder = get_encoder(model)

    for table_name in table_names:
        try:
            cursor.execute(f'SELECT * FROM "{table_name}" LIMIT {row_num}')
            rows = cursor.fetchall()

            column_names = [description[0] for description in cursor.description]

            insert_statements = []
            for row in rows:
                values = []
                for value in row:
                    if value is None:
                        values.append("NULL")
                    elif isinstance(value, str):
                        escaped_value = value.replace("'", "''")
                        values.append(f"'{escaped_value}'")
                    else:
                        values.append(str(value))

                insert_statement = (
                    f"INSERT INTO {table_name} ({', '.join(column_names)}) "
                    f"VALUES ({', '.join(values)});"
                )

                if len(encoder.encode(insert_statement)) <= max_stmt_tokens:
                    insert_statements.append(insert_statement)

            table_name2insert_statements[table_name] = insert_statements

        except Exception as e:
            print(e)

    cursor.close()
    conn.close()

    return table_name2insert_statements

def get_table_names(db_file: str) -> List[str]:
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    table_names = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return table_names

def obtain_m_schema(db_file_dir, db_name):
    mschema_path = os.path.join(os.path.dirname(db_file_dir), f'{db_name}_mschema.txt')

    # table_names, create_statements = obtain_db_schema(db_file_dir)
    # schema_str = ""
    # for table_name, create_statement in zip(table_names, create_statements):
    #     schema_str += f"Table {table_name}:\n{create_statement}\n\n"
    # return schema_str.strip()
    # 1.connect to the database engine
    db_path = db_file_dir
    abs_path = os.path.abspath(db_path)
    if not os.path.exists(abs_path):
        print(f"Database file not found for {db_name}, path: {abs_path}")
        return ""
    db_engine = create_engine(f'sqlite:///{abs_path}')

    # 2.Construct M-Schema
    schema_engine = SchemaEngine(engine=db_engine, db_name=db_name)
    mschema = schema_engine.mschema
    mschema_str = mschema.to_mschema()
    with open(mschema_path, 'w', encoding='utf-8') as f:
        f.write(mschema_str)
    mschema_jsonpath = os.path.join(os.path.dirname(db_file_dir), f'{db_name}_mschema.json')
    mschema.save(mschema_jsonpath)
    return mschema_str.strip()


def build_prompt_for_db(db_root, db_name, prompt_template, turn_range, db_engine_name="SQLite"):
    db_file = os.path.join(db_root, db_name, f"{db_name}.sqlite")
    if not os.path.exists(db_file):
        raise FileNotFoundError(f"db file not found: {db_file}")

    table_names = get_table_names(db_file)
    schema_str = obtain_m_schema(db_file, db_name)
    table_name2insert_statements = obtain_insert_statements(db_file, table_names, row_num=3)

    # 这里就是你要替换的逻辑
    insert_statements = []
    for table_name in table_names:
        insert_statements += table_name2insert_statements.get(table_name, [])

    # if len(insert_statements) == 0:
    #     db_value_prompt = ""
    # else:
    #     if len(insert_statements) > 3:
    #         insert_statements = random.sample(insert_statements, 3)
    if insert_statements:
        db_value_prompt = insert_stmts_template.format(
            insert_statements="\n\n".join(insert_statements)
        )
    else:
        db_value_prompt = "Sample values: Not provided."

    prompt = prompt_template
    prompt = prompt.replace("{{SCHEMA}}", schema_str)
    prompt = prompt.replace("{{SAMPLE_VALUES}}", db_value_prompt)
    prompt = prompt.replace("{{TURN_RANGE}}", turn_range)
    prompt = prompt.replace("{{SQL_DIALECT}}", db_engine_name)
    return prompt.strip(), db_value_prompt.strip(), schema_str.strip()


def collect_db_names(db_root):
    db_names = []
    for name in sorted(os.listdir(db_root)):
        subdir = os.path.join(db_root, name)
        db_file = os.path.join(subdir, f"{name}.sqlite")
        if os.path.isdir(subdir) and os.path.exists(db_file):
            db_names.append(name)
    return db_names


def process_one_db(db_root, db_name, model, turn_range):
    prompt, db_value_prompt, schema_str = build_prompt_for_db(
        db_root=db_root,
        db_name=db_name,
        prompt_template=PROMPT,
        turn_range=turn_range,
        db_engine_name="SQLite"
    )
    if prompt:
        print(f"Processing db: {db_name}, turn_range: {turn_range}")
    response = openai_chat(prompt, model=model)
    return {
        "db_id": db_name,
        "turn_range": turn_range,
        "prompt": prompt,
        "db_value_example": db_value_prompt,
        "m_schema": schema_str,
        "response": response
    }


def _parse_response_field(result: dict) -> dict:
    if not isinstance(result, dict):
        return result

    response = result.get("response")

    if isinstance(response, dict):
        return result

    # 列表且只有一个字符串元素，尝试解析该字符串
    if isinstance(response, list):
        if len(response) == 1 and isinstance(response[0], str):
            result["response"] = response[0]
            response = response[0]
        else:
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

def is_valid_description(desc, start_turn_id=1):
    """
    检查 description 是否合法：
    1. 必须包含这些字段
    2. Core Tables Involved / Key Relationships Between Tables / Main Thread of the Multi-turn Dialogue 必须是 list
    3. Main Thread 中每一项必须有 turn_id / operator_type / plan
    4. Main Thread 的 turn_id 必须从 start_turn_id 开始依次递增
    """
    if not isinstance(desc, dict):
        return False

    required_fields = [
        "Business Scenario",
        "User Overall Goal",
        "Main Thread of the Multi-turn Dialogue",
    ]

    for field in required_fields:
        if field not in desc:
            return False

    if not isinstance(desc["Main Thread of the Multi-turn Dialogue"], list):
        return False

    main_thread = desc["Main Thread of the Multi-turn Dialogue"]
    if len(main_thread) == 0:
        return False

    turn_ids = []
    for turn in main_thread:
        if not isinstance(turn, dict):
            return False

        if "turn_id" not in turn or "operator_type" not in turn or "plan" not in turn:
            return False

        try:
            turn_id = int(turn["turn_id"])
        except Exception:
            return False

        if not str(turn["operator_type"]).strip():
            return False
        if not str(turn["plan"]).strip():
            return False

        turn_ids.append(turn_id)

    expected_turn_ids = list(range(start_turn_id, start_turn_id + len(main_thread)))
    if turn_ids != expected_turn_ids:
        return False

    return True


def plan_synthesis(
    db_path: str,
    output_dir: str,
    model: str = "gpt-5.2",
    max_workers: int = 10,
    output_file: str = "intent_results.json",
    turn_ranges=None
):
    if turn_ranges is None:
        turn_ranges = ["3-4 turns", "5-6 turns", "7-8 turns"]

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, output_file)

    db_names = collect_db_names(db_path)
    if not db_names:
        raise FileNotFoundError(f"No valid databases found under {db_path}")

    print(f"Found {len(db_names)} databases.")
    print(f"Turn ranges: {turn_ranges}")
    print(f"Total tasks: {len(db_names) * len(turn_ranges)}")


    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {
            executor.submit(process_one_db, db_path, db_name, model, turn_range): (db_name, turn_range)
            for db_name in db_names
            for turn_range in turn_ranges
        }

        for future in tqdm(
            concurrent.futures.as_completed(future_to_task),
            total=len(future_to_task)
        ):
            db_name, turn_range = future_to_task[future]
            try:
                result = future.result()
            except Exception as e:
                result = {
                    "db_id": db_name,
                    "turn_range": turn_range,
                    "prompt": None,
                    "response": None,
                    "error": str(e),
                    "traceback": traceback.format_exc()
                }
            result = _parse_response_field(result)
            result['description'] = result.pop('response')
            result['dialogue'] = []
            # result.pop('prompt', None)
            result['current_turn_id'] = 1
            if is_valid_description(result["description"], start_turn_id=1):
                results.append(result)
            else:
                print(f"Invalid description for db: {db_name}, turn_range: {turn_range}, result['description']: {result['description']}")

    results = sorted(results, key=lambda x: (x["db_id"], x["turn_range"]))

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Plan synthesis results saved to: {output_path}")
    return output_path, results