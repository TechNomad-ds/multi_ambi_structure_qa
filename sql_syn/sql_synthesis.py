import json
import os
import random
import sqlite3
import numpy as np
import argparse
import importlib
import requests
import concurrent.futures
import tiktoken
import re
import sys
import time
import multiprocessing as mp
import ijson
import itertools

from tqdm import tqdm
from pathlib import Path
from datetime import datetime
from func_timeout import func_timeout, FunctionTimedOut
from openai import OpenAI
from config import API_URL, API_KEY
from M_Schema.schema_engine import SchemaEngine
from sqlalchemy import create_engine


def make_timestamp_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]

seed = 42

def obtain_insert_statements(db_file_dir, table_names):
    table_name2insert_statements = dict()
    conn = sqlite3.connect(db_file_dir)
    cursor = conn.cursor()

    for table_name in table_names:
        try:
            cursor.execute(f'SELECT * FROM "{table_name}" LIMIT 2')
            rows = cursor.fetchall()

            column_names = [description[0] for description in cursor.description]

            insert_statements = []
            for row in rows:
                values = ', '.join([f"'{str(value)}'" if isinstance(value, str) else str(value) for value in row])
                insert_statement = f"INSERT INTO {table_name} ({', '.join(column_names)}) VALUES ({values});"
                insert_statements.append(insert_statement)

            # for statement in insert_statements:
            #     print(statement)
            table_name2insert_statements[table_name] = insert_statements

        except Exception as e:
            print(e)

    cursor.close()
    conn.close()

    return table_name2insert_statements


# step1: generate prompts
def build_single_prompt(item, sql_dialect = "SQLite"):
    from sql_syn.prompt_templates.prompt import PROMPT
    # ---------- 1) 读取并解析 description ----------
    description = item["description"]

    business_scenario = str(description["Business Scenario"]).strip()
    overall_goal = str(description["User Overall Goal"]).strip()
    normalized_thread = description["Main Thread of the Multi-turn Dialogue"]

    # ---------- 由 Main Thread 决定总 turns，由 dialogue 决定当前 turn ----------
    dialogue = item.get("dialogue", []) or []

    total_turns = len(normalized_thread)
    if total_turns == 0:
        raise ValueError("Main Thread of the Multi-turn Dialogue 为空，无法确定总 turn 数")

    current_turn_id = item.get("current_turn_id", 1)

    # 如果 dialogue 已经到最后一轮，就没有下一轮可生成了
    if current_turn_id > total_turns:
        raise ValueError(
            f"dialogue 已覆盖到第 {current_turn_id} 轮，但 Main Thread 只有 {total_turns} 轮"
        )

    # 找到当前 turn
    if current_turn_id == normalized_thread[current_turn_id - 1]["turn_id"]:
        current_plan = normalized_thread[current_turn_id - 1]["plan"]
        current_operator_type = normalized_thread[current_turn_id - 1]["operator_type"]
    else:
        raise ValueError(f"无法在 Main Thread 中定位 current_turn_id={current_turn_id}")

    # ---------- 组织 DIALOGUE_CONTEXT ----------
    dialogue_list = []
    for d in dialogue:
        q = d["question"]
        sql = d["sql"]
        desc = d["plan"]
        if q or sql:
            part = f"Question {d['turn_id']}: {q}\nSQL {d['turn_id']}: {sql}\nDescription {d['turn_id']}: {desc}"
            dialogue_list.append(part)
    dialogue_context = "\n\n".join(dialogue_list)

    # 如果有对话历史，选最后一轮执行后的 m_schema（如果有的话），否则选 item["m_schema"]
    if dialogue and "m_schema" in dialogue[-1] and dialogue[-1]["m_schema"]:
        m_schema_str = dialogue[-1]["m_schema"]
    else:
        m_schema_str = item["m_schema"] 
    if not m_schema_str.strip():
        raise ValueError("没有可用的 m_schema 信息")

    # ---------- 5) 填充 PROMPT ----------
    mapping = {
        "BUSINESS_SCENARIO": business_scenario or "Not provided.",
        "OVERALL_GOAL": overall_goal or "Not provided.",
        "DIALOGUE_CONTEXT": dialogue_context or "None.",
        "SCHEMA": m_schema_str,
        "SAMPLE_VALUES": item.get("db_value_example", "Not provided."),
        "CURRENT_TURN_PLAN": current_plan,
        "SQL_TYPE": current_operator_type,
        "SQL_DIALECT": sql_dialect,
    }

    prompt = PROMPT
    for k, v in mapping.items():
        prompt = prompt.replace(f"{{{{{k}}}}}", v)

    item["sql_prompt"] = prompt.strip()

    return prompt, item

def generate_prompt(db_path: str, input_path: str, output_dir: str, output_file: str):
    """
    Generate prompts and write to output_file.
    Returns: (out_path: Path, prompts: list[dict])
    """
    with open(input_path, "r", encoding="utf-8") as f:
        history_data = json.load(f)

    prompt_data = []
    for history in tqdm(history_data):
        try:
            history.pop("prompt", None)
            prompt, new_item = build_single_prompt(history)
            prompt_data.append(new_item)
        except Exception as e:
            history["prompt_error"] = str(e)
            prompt_data.append(history)

    data_id = make_timestamp_id()
    output_dir = Path(output_dir)
    out_path = output_dir / f"results_{data_id}" / output_file
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(prompt_data, indent=2, ensure_ascii=False))

    return out_path, prompt_data, data_id



api_url = API_URL
api_key = API_KEY

def parse_sql(response):
    pattern = r"```sql\s*(.*?)\s*```"
    
    sql_blocks = re.findall(pattern, response, re.DOTALL)

    if sql_blocks:
        # Extract the last SQL query in the response text and remove extra whitespace characters
        last_sql = sql_blocks[-1].strip()
        return last_sql
    else:
        print("No SQL blocks found.")
        return ""
    
# def openai_chat(messages, model='gpt-4o', max_retry=3):
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
#                 "temperature": 0.6
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

def openai_chat(messages, model='gpt-5.5', n=1, max_retry=3):
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

def get_encoder(model: str = 'gpt-4o'):
    """获取 tiktoken 编码器，按模型选择最合适的编码。"""
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        # 如果模型不被识别，就退回到通用 o200k_base
        enc = tiktoken.get_encoding("o200k_base")
    return enc

def count_tokens(text: str, model: str) -> int:
    """统计单个文本的 token 数。"""
    ENCODER = get_encoder(model)
    return len(ENCODER.encode(text))

def llm_inference(model, history_data, max_workers=10):
    prompts = [data["sql_prompt"] for data in history_data]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        responses = list(tqdm(executor.map(openai_chat, prompts, itertools.repeat(model)), total=len(prompts)))

    print(f"len(prompts): {len(prompts)}")
    print(f"Generated {len(responses)} responses.")
    assert len(prompts) == len(responses) == len(history_data)
    for i, p in enumerate(prompts):
        n_tokens = count_tokens(p, model)
        token_limit_for_prompt = 819600
        if n_tokens > token_limit_for_prompt:
            print(
                f"[超过限制] idx={i}, tokens={n_tokens} > {token_limit_for_prompt}"
            )
    for item, resp in zip(history_data, responses):
        if isinstance(resp, list):
            resp = next((s for s in resp if isinstance(s, str) and s.strip()), "")
        item["sql_response"] = resp

    return history_data


# step2: synthesize sqls
def synthesize_sql(model: str, input_file: str, output_file: str, sql_file: str, prompts_dir: str, results_dir: str, data_id: str):
    """
    Read prompts dataset from prompts_dir, run LLM inference
    Returns: (results_path: Path, sqls_path: Path, results: list, sqls: list)
    """
    prompts_dir = Path(prompts_dir)
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    input_path = prompts_dir / f"results_{data_id}" / input_file
    output_path = results_dir / f"results_{data_id}" / output_file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sqls_path = results_dir / f"results_{data_id}" / sql_file
    sqls_path.parent.mkdir(parents=True, exist_ok=True)

    # 读入数据集
    with open(input_path, "r", encoding="utf-8") as f:
        input_dataset = json.load(f)

    # 推理
    results = llm_inference(model, input_dataset)
    print(f"len(results): {len(results)}")

    # 写完整 results
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(results, indent=2, ensure_ascii=False))

    # 解析 SQL 并写 sqls
    for idx, result in enumerate(results):
        sql_response = result.get("sql_response", "")
        if isinstance(sql_response, list):
            sql_response = next((s for s in sql_response if isinstance(s, str) and s.strip()), "")
        new_sql = parse_sql(sql_response)
        result["new_sql"] = new_sql

    with open(sqls_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    return output_path, sqls_path, results


  
def load_json_file(file):
    dataset = []
    with open(file, 'r', encoding='utf-8') as f:
        objects = ijson.items(f, 'item')
        for obj in tqdm(objects):
            dataset.append(obj)
    return dataset

# def strip_sql_comments(sql):
#     # 去掉块注释 /* ... */ 和行注释 -- ...
#     sql_wo_comments = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
#     sql_wo_comments = re.sub(r'--.*', '', sql_wo_comments)
#     return sql_wo_comments

# def filter_dml_sqls(synthesized_sqls, data_type):
#     def_sqls = []
#     for sql_info in tqdm(synthesized_sqls):
#         sql = sql_info["sql"]
#         sql_wo_comments = strip_sql_comments(sql).strip().lower()
#         if data_type == "insert":
#             if sql_wo_comments.startswith("insert") or sql_wo_comments.startswith("with") or sql_wo_comments.startswith("replace"):
#                 def_sqls.append(sql_info)
#         else:
#             if sql_wo_comments.startswith(data_type):
#                 def_sqls.append(sql_info)
#     return def_sqls

class SQLExecutionError(Exception):
    pass


def obtain_m_schema(db_file_dir, db_name, example_num=5):
    mschema_path = os.path.join(os.path.dirname(db_file_dir), f"{db_name}_mschema.txt")
    db_path = db_file_dir
    abs_path = os.path.abspath(db_path)
    if not os.path.exists(abs_path):
        print(f"Database file not found for {db_name}, path: {abs_path}")
        return ""

    db_engine = create_engine(f"sqlite:///{abs_path}")
    schema_engine = SchemaEngine(engine=db_engine, db_name=db_name)
    mschema = schema_engine.mschema
    mschema_str = mschema.to_mschema(example_num=example_num)

    with open(mschema_path, "w", encoding="utf-8") as f:
        f.write(mschema_str)

    mschema_jsonpath = os.path.join(os.path.dirname(db_file_dir), f"{db_name}_mschema.json")
    mschema.save(mschema_jsonpath)
    return mschema_str.strip()

def run_on_memory(db_path, sql):
    disk_conn = None
    mem_conn = None
    try:
        # 打开磁盘库（只读用来备份）
        disk_conn = sqlite3.connect(db_path)
        # 创建内存库
        mem_conn = sqlite3.connect(":memory:")

        # 把磁盘库的内容完整备份到内存库
        disk_conn.backup(mem_conn)

        cur = mem_conn.cursor()

        if sql.strip():
            try:
                cur.executescript(sql)
            except Exception as e:
                raise SQLExecutionError(f"SQL failed: {e}") from e

        mem_conn.commit()
    finally:
        if mem_conn is not None:
            mem_conn.close()
        if disk_conn is not None:
            disk_conn.close()


def run_on_memory_with_snapshot(db_path, sql):
    disk_conn = None
    mem_conn = None
    try:
        disk_conn = sqlite3.connect(db_path)
        mem_conn = sqlite3.connect(":memory:")
        disk_conn.backup(mem_conn)

        cur = mem_conn.cursor()
        if sql.strip():
            try:
                cur.executescript(sql)
            except Exception as e:
                raise SQLExecutionError(f"SQL failed: {e}") from e

        mem_conn.commit()

        # 将执行后的内存库状态备份到临时文件，再用 SchemaEngine 生成 m_schema
        tmp_db_path = f"{db_path}.postprocess_tmp_{os.getpid()}_{int(time.time() * 1000)}.sqlite"
        tmp_conn = None
        try:
            tmp_conn = sqlite3.connect(tmp_db_path)
            mem_conn.backup(tmp_conn)
            tmp_conn.commit()
        finally:
            if tmp_conn is not None:
                tmp_conn.close()

        try:
            db_name = os.path.splitext(os.path.basename(db_path))[0]
            return obtain_m_schema(tmp_db_path, db_name)
        finally:
            try:
                if os.path.exists(tmp_db_path):
                    os.remove(tmp_db_path)
            except Exception:
                pass

    finally:
        if mem_conn is not None:
            mem_conn.close()
        if disk_conn is not None:
            disk_conn.close()


# def filter_executable_sqls(synthesized_sqls, db_dir):
#     for idx, sql_info in enumerate(tqdm(synthesized_sqls)):
#         db_id = sql_info["db_id"]
#         db_path = os.path.join(db_dir, db_id, db_id + ".sqlite")

#         if sql_info.get("continue_decision") is not True:
#             continue

#         ex_sql = sql_info.get("sql_info", {}).get("generated_sql", "")

#         if not ex_sql.strip():
#             continue

#         try:
#             run_on_memory(db_path, ex_sql)
    
#         except SQLExecutionError as e:
#             print(f"{idx} SQL execution error, skipped. detail: {e}")
#             sql_info["continue_decision"] = False
#             continue

#         except Exception as e:
#             # 其他异常
#             print(f"{idx} UNKNOWN execution error, skipped. detail: {e}")
#             sql_info["continue_decision"] = False
#             continue

#     return synthesized_sqls

# def execute_wrapper(sample_idx, db_id, sql, complexity, timeout, db_dir):
#     db_path = os.path.join(db_dir, db_id, db_id + ".sqlite")
#     try:
#         func_timeout(timeout, run_on_memory, args=(db_path, sql))
#         return [sample_idx, db_id, sql, complexity, 1]
#     except KeyboardInterrupt:
#         sys.exit(0)
#     except FunctionTimedOut:
#         print(f"Sample {sample_idx} timed out.")
#         return [sample_idx, db_id, sql, complexity, 0]
#     except Exception:
#         print(f"Sample {sample_idx} execution error.")
#         return [sample_idx, db_id, sql, complexity, 0]

# def remove_timeout_sqls_parallel(synthesized_sqls, db_dir, out_list, num_cpus=120, timeout=1):
#     def execute_callback(result):
#         sample_idx, db_id, sql, complexity, valid_flag = result
#         if valid_flag == 1:
#             out_list.append(
#                 {
#                     "db_id": db_id,
#                     "sql": sql,
#                     "complexity": complexity,
#                 }
#             )
            
#     parallel_batch_size = 1024
#     batches = [synthesized_sqls[i: i + parallel_batch_size] for i in range(0, len(synthesized_sqls), parallel_batch_size)]
#     assert len(synthesized_sqls) == sum(len(b) for b in batches)

#     for batch_idx, batch_sqls in enumerate(batches):
#         print(f"execution process: {batch_idx + 1}/{len(batches)}")
#         pool = mp.Pool(processes=num_cpus)
#         for sample_idx, sql_info in enumerate(batch_sqls):
#             pool.apply_async(
#                 execute_wrapper,
#                 args=(
#                     sample_idx,
#                     sql_info["db_id"],
#                     sql_info["sql"],
#                     sql_info["complexity"],
#                     timeout,
#                     db_dir,
#                 ),
#                 callback=execute_callback,
#             )
#         pool.close()
#         pool.join()
#         time.sleep(10)

def validate_sql_with_timeout(db_path, sql, timeout=120, with_snapshot=False):
    if not sql or not str(sql).strip():
        return False, "Empty SQL."

    if not os.path.exists(db_path):
        return False, f"Database file not found: {db_path}"

    try:
        if with_snapshot:
            executed_m_schema = func_timeout(timeout, run_on_memory_with_snapshot, args=(db_path, sql))
            return True, executed_m_schema
        else:
            func_timeout(timeout, run_on_memory, args=(db_path, sql))
            return True, None
    except FunctionTimedOut:
        return False, f"SQL execution timed out after {timeout}s."
    except SQLExecutionError as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def build_execution_sql_with_dialogue(item, current_sql):
    """
    按顺序拼接 dialogue 历史 SQL，再追加当前候选 SQL，作为执行校验脚本。
    """
    ordered_sqls = []

    dialogue = item.get("dialogue", []) or []
    for turn in dialogue:
        hist_sql = (turn.get("sql") or "").strip()
        if hist_sql:
            hist_sql = hist_sql.rstrip(';') + ';'
            ordered_sqls.append(hist_sql)

    curr = (current_sql or "").strip()
    if curr:
        curr = curr.rstrip(';') + ';'
        ordered_sqls.append(curr)

    return "\n\n".join(ordered_sqls)


def build_retry_prompt(base_prompt, fail_history, keep_last_n=3):
    """
    把最近几次失败 SQL + 错误信息拼回原始 prompt，用于重试。
    """
    fail_history = fail_history[-keep_last_n:]

    feedback_blocks = []
    for i, h in enumerate(fail_history, 1):
        failed_sql = h["sql"]
        error_msg = h.get("error", "") or "Unknown error."

        if failed_sql.strip():
            block = (
                f"Previous failed attempt {i}:\n"
                f"Error: {error_msg}\n"
                f"Failed SQL:\n```sql\n{failed_sql}\n```"
            )
        else:
            block = (
                f"Previous failed attempt {i}:\n"
                f"Error: {error_msg}\n"
                f"No valid SQL was extracted from the previous response."
            )
        feedback_blocks.append(block)

    feedback_text = "\n\n".join(feedback_blocks)

    retry_instruction = f"""
### Previous failed attempts
{feedback_text}

Please regenerate the SQL for the SAME current turn.

Requirements:
- Keep the business scenario, overall goal, and current-turn intent unchanged.
- Fix the execution / parsing problem shown above.
- Only output ONE executable SQL in a single ```sql ... ``` code block.
- Do not output explanation.
""".strip()

    return base_prompt.rstrip() + "\n\n" + retry_instruction + "\n"

# step3: postprocess sqls
# def postprocess_sql( db_path: str, input_file: str, output_file: str, results_dir: str, data_id: str,
#                     num_cpus: int = 10, timeout: int = 2):
#     """
#     Postprocess LLM SQL results:
#     Returns: (output_path: Path, synthesized_sqls: list[dict])
#     """
#     results_dir = Path(results_dir)
#     results_dir.mkdir(parents=True, exist_ok=True)

#     db_dir = str(db_path)
#     input_path = results_dir / f"results_{data_id}" / input_file
#     input_path.parent.mkdir(parents=True, exist_ok=True)
#     output_path = results_dir / f"results_{data_id}" / output_file
#     output_path.parent.mkdir(parents=True, exist_ok=True)

#     synthesized_sqls = load_json_file(str(input_path))

#     count_continue_decision(synthesized_sqls)

#     # 过滤无法执行的 SQL（基于内存库执行）
#     synthesized_sqls = filter_executable_sqls(synthesized_sqls, db_dir)
#     count_continue_decision(synthesized_sqls)
#     # print(f"SQL num after filtering non-executable SQLs:", len(synthesized_sqls))

#     # 并行移除超时 SQL
#     # manager = mp.Manager()
#     # no_timeout_synthesized_sqls = manager.list()

#     # remove_timeout_sqls_parallel(
#     #     synthesized_sqls,
#     #     db_dir,
#     #     out_list=no_timeout_synthesized_sqls,
#     #     num_cpus=num_cpus,
#     #     timeout=timeout,
#     # )

#     # synthesized_sqls = list(no_timeout_synthesized_sqls)
#     # print("sql num after removing timeout sqls:", len(synthesized_sqls))

#     # 保存结果
#     with open(output_path, "w", encoding="utf-8") as f:
#         f.write(json.dumps(synthesized_sqls, indent=2, ensure_ascii=False))

#     return output_path, synthesized_sqls

def postprocess_one(item, db_dir, timeout=120, model="gpt-4o", retry_k=3):
    db_id = item["db_id"]
    db_file = os.path.join(db_dir, db_id, db_id + ".sqlite")

    base_prompt = item.get("sql_prompt", "") or ""
    original_response = item.get("sql_response", "") or ""
    if isinstance(original_response, list):
        original_response = next((s for s in original_response if isinstance(s, str) and s.strip()), "")
    current_sql = item.get("new_sql", "") or ""

    fail_history = []
    attempts = []
    final_sql = None
    final_response = original_response
    fail_reason = None
    executed_m_schema = ""

    # ---------- 先校验第一次生成的 SQL ----------
    execution_sql = build_execution_sql_with_dialogue(item, current_sql)
    ok, validation_result = validate_sql_with_timeout(
        db_file,
        execution_sql,
        timeout=timeout,
        with_snapshot=True,
    )
    err = None if ok else validation_result
    attempts.append(
        {
            "attempt_id": 0,
            "source": "initial_generation",
            "sql": current_sql,
            "raw_response": original_response,
            "success": ok,
            "error": err,
        }
    )

    if ok:
        final_sql = current_sql
        executed_m_schema = validation_result or ""
    else:
        fail_reason = err
        fail_history.append(
            {
                "sql": current_sql,
                "error": err,
                "dialogue_sql": execution_sql,
            }
        )

        # ---------- 重试生成 ----------
        for retry_idx in range(1, retry_k + 1):
            print(f"Retry attempt {retry_idx} for db_id={db_id} due to: {fail_reason}")
            if not base_prompt.strip():
                fail_reason = f"No sql_prompt found for retry. Last error: {fail_reason}"
                break

            retry_prompt = build_retry_prompt(base_prompt, fail_history, keep_last_n=3)

            retry_response = openai_chat(retry_prompt, model=model, max_retry=3)
            if isinstance(retry_response, list):
                retry_response = next((s for s in retry_response if isinstance(s, str) and s.strip()), "")
            retry_sql = parse_sql(retry_response or "")

            retry_execution_sql = build_execution_sql_with_dialogue(item, retry_sql)
            ok_retry, retry_validation_result = validate_sql_with_timeout(
                db_file,
                retry_execution_sql,
                timeout=timeout,
                with_snapshot=True,
            )
            err_retry = None if ok_retry else retry_validation_result

            attempts.append(
                {
                    "attempt_id": retry_idx,
                    "source": f"retry_{retry_idx}",
                    "sql": retry_sql,
                    "raw_response": retry_response,
                    "success": ok_retry,
                    "error": err_retry,
                }
            )

            if ok_retry:
                final_sql = retry_sql
                final_response = retry_response
                fail_reason = None
                executed_m_schema = retry_validation_result or ""
                break
            else:
                fail_reason = err_retry
                fail_history.append(
                    {
                        "sql": retry_sql,
                        "error": err_retry,
                        "dialogue_sql": retry_execution_sql,
                    }
                )

    final_fail_reason = None
    if final_sql is None:
        final_fail_reason = fail_reason or f"Failed after {retry_k} retries."

    item["sql_postprocess_info"] = {
        "attempts": attempts,
        "retry_k": retry_k,
        "fail_reason": final_fail_reason,
    }

    if final_sql is not None:
        item["new_sql"] = final_sql
        item["sql_response"] = final_response
        item["sql_synthesis"] = {
            "new_sql": final_sql,
            "sql_response": final_response,
            "sql_status": "success",
            "executed_m_schema": executed_m_schema,
        }
    else:
        item["sql_synthesis"] = {
            "new_sql": "",
            "sql_status": "failed",
        }

    return item


def postprocess_sql(
    db_path: str,
    input_file: str,
    output_file: str,
    results_dir: str,
    data_id: str,
    num_cpus: int = 25,
    timeout: int = 120,
    model: str = "gpt-4o",
    retry_k: int = 3,
):
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    db_dir = str(db_path)
    input_path = results_dir / f"results_{data_id}" / input_file
    output_path = results_dir / f"results_{data_id}" / output_file
    output_path.parent.mkdir(parents=True, exist_ok=True)

    synthesized_sqls = load_json_file(str(input_path))

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_cpus) as executor:
        synthesized_sqls = list(
            tqdm(
                executor.map(
                    lambda x: postprocess_one(
                        x,
                        db_dir=db_dir,
                        timeout=timeout,
                        model=model,
                        retry_k=retry_k,
                    ),
                    synthesized_sqls,
                ),
                total=len(synthesized_sqls),
                desc="Postprocessing & retrying SQL",
            )
        )

    success_count = sum(1 for x in synthesized_sqls if x["sql_synthesis"]["sql_status"] == "success")
    fail_count = sum(1 for x in synthesized_sqls if x["sql_synthesis"]["sql_status"] == "failed")

    print(f"Postprocess done. Total: {len(synthesized_sqls)}, Success: {success_count}, Failed: {fail_count}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(synthesized_sqls, indent=2, ensure_ascii=False))

    return output_path, synthesized_sqls



def sql_synthesis(db_path: str, input_path: str, output_dir: str, model: str = "gpt-4o"):
    out_path, data, data_id = generate_prompt(db_path=db_path, input_path=input_path, output_dir=output_dir, output_file="sql_synthesis_prompts.json")
    print(f"Prompts saved to: {out_path}")
    results_path, sqls_path, results= synthesize_sql(model=model, input_file="sql_synthesis_prompts.json",
                                            output_file="sql_synthesis_results.json", sql_file="synthesized_sqls.json",
                                            prompts_dir=output_dir, results_dir=output_dir, data_id=data_id)
    print(f"LLM results saved to: {results_path}")
    print(f"Synthesized SQLs saved to: {sqls_path}")
    final_path, synthesized_sqls = postprocess_sql(db_path=db_path, input_file="synthesized_sqls.json", output_file="synthetic_sqls.json", 
                                                   results_dir=output_dir, data_id=data_id, model=model)
    print(f"Post-processed SQLs saved to: {final_path}")
    return final_path, synthesized_sqls, data_id
