
# 以下是测试用例合成的要求：
# 对读取的数据中符合SQL类型的数据构造提示词，对所有数据的所有turn合成测试用例
# 然后进行后处理：合成后执行text2sql中SQL和相应的测试用例，检验gold SQL能否通过测试用例，注意这一过程中gold SQL对数据库的影响需要保留（即执行gold SQL后不复原数据库），但是测试用例执行完后需要恢复数据库到gold SQL执行后的状态以进行下一个turn的验证，如果失败则重试k次后放弃，如果成功则继续直到达到最大turn数
# 尽量提高效率，能并行的就并行，但注意数据库的状态需要正确维护

# 以下提供sql合成的代码框架包括提示词构造、生成、后处理等步骤作为代码实现参考，具体逻辑以以上要求为准：

import json
import os
import re
import sqlite3
import concurrent.futures
from pathlib import Path
from tqdm import tqdm
from func_timeout import func_timeout, FunctionTimedOut

from config import API_URL, API_KEY
from testcase_syn.prompt_templates.prompt import PROMPT_NOTYPE
import requests

def make_timestamp_id() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")

session = requests.Session()

def openai_chat(messages, model='gpt-4o', max_retry=3):
    headers = {
        'Authorization': f"Bearer {API_KEY}",
        'Content-Type': 'application/json',
        'User-Agent': 'Apifox/1.0.0 (https://apifox.com)'
    }
    for attempt in range(max_retry):
        try:
            payload = json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a specialized Database Engineer testing Text-to-SQL."},
                    {"role": "user", "content": messages}
                ],
                "temperature": 0.2 
            })
            # response = requests.post(API_URL, headers=headers, data=payload, timeout=60)
            response = session.post(API_URL, headers=headers, data=payload)
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
        except Exception as e:
            print(f"API Error: {e} (Attempt {attempt + 1}/{max_retry})")
    return None

def parse_python(response: str) -> str:
    """从 LLM 返回内容中提取 Python 代码"""
    pattern = r"```python\s*(.*?)\s*```"
    blocks = re.findall(pattern, response, re.DOTALL)
    if blocks:
        return blocks[-1].strip()
    return response.strip()

def parse_text(response: str) -> str:
    """从 LLM 返回内容中提取 Python 代码"""
    pattern = r"```text\s*(.*?)\s*```"
    blocks = re.findall(pattern, response, re.DOTALL)
    if blocks:
        return blocks[-1].strip()
    return response.strip()

def infer_sql_type(sql: str) -> str:
    """根据 SQL 文本推断操作类型。"""
    if not sql:
        return "SELECT"
    s = sql.strip().upper()
    if s.startswith("SELECT"):
        return "SELECT"
    if s.startswith("INSERT"):
        return "INSERT"
    if s.startswith("UPDATE"):
        return "UPDATE"
    if s.startswith("DELETE"):
        return "DELETE"
    if s.startswith("CREATE VIEW"):
        return "CREATE VIEW"
    if s.startswith("CREATE TABLE"):
        return "CREATE TABLE"
    if s.startswith("ALTER TABLE"):
        return "ALTER TABLE"
    if s.startswith("CREATE TRIGGER"):
        return "CREATE TRIGGER"
    if s.startswith("CREATE INDEX"):
        return "CREATE INDEX"
    if s.startswith("DROP"):
        return "DROP"
    return None


# 1、 Prompt 生成
def build_testcase_prompt(item, turn, sql_dialect="SQLite"):
    description = item["description"]
    normalized_thread = description.get("Main Thread of the Multi-turn Dialogue", [])
    turn_id = turn["turn_id"]

    sql_type = None
    if isinstance(turn_id, int) and 1 <= turn_id <= len(normalized_thread):
        sql_type = normalized_thread[turn_id - 1]["operator_type"]

    # 回退：没有可用的 operator_type 时根据 SQL 推断
    if not sql_type:
        sql_type = infer_sql_type(turn["sql"])
    sql_type = str(sql_type).upper()

    if sql_type == "SELECT":
        turn["sql_type"] = sql_type
        return turn

    # 这里应该获取上一个turn的 m_schema 来反映最新的 schema 状态，第一个turn使用item级的 m_schema 作为当前 turn 的 schema 信息
    if turn_id == 1:
        schema_str = item["m_schema"]
    else:
        dialogue = item["dialogue"]
        previous_turn = next((t for t in dialogue if t["turn_id"] == turn_id - 1), None)
        schema_str = previous_turn["m_schema"] if previous_turn else item["m_schema"]
    question = turn["question"]
    gold_sql = turn["sql"]

    mapping = {
        "db_engine": sql_dialect,
        "SCHEMA": schema_str,
        "SQL_TYPE": sql_type,
        "NATURAL_LANGUAGE_QUESTION": question,
        "SQL_STATEMENT": gold_sql,
        # "FEW_SHOT_EXAMPLES": ""
    }
    
    prompt = PROMPT_NOTYPE
    for k, v in mapping.items():
        prompt = prompt.replace(f"{{{{{k}}}}}", str(v))

    turn["testcase_prompt"] = prompt.strip()
    turn["sql_type"] = sql_type
    return turn

# Step 2 & 3: 辅助函数与测试用例执行环境
def execute_queries(sqls, conn):
    """注入到执行环境中的辅助函数，对齐 prompt 要求"""
    cursor = conn.cursor()
    results = []
    err = None
    try:
        for sql in sqls:
            if not sql.strip(): continue
            cursor.execute(sql)
            if cursor.description:
                results.append(cursor.fetchall())
            else:
                results.append([]) # 对于非查询类语句返回空列表
        conn.commit()
    except Exception as e:
        err = str(e)
    # prompt 要求解包为 actual_result, execution_error
    return (results[-1] if results else None), err

def run_testcase_sandbox(code, pred_sql, sol_sql, conn):
    """在一个隔离的作用域内执行动态生成的测试代码"""
    loc = {
        'execute_queries': execute_queries,
        'json': json,
        're': re
    }
    try:
        exec(code, globals(), loc)
        if 'test_case' not in loc:
            return False, "Error: 'test_case' function not found in the generated code."
        
        test_func = loc['test_case']
        # 执行生成的测试函数
        res = test_func(pred_sql, sol_sql, conn)
        
        if res == 1:
            return True, None
        else:
            return False, f"Test failed: Function returned {res} instead of 1."
    except AssertionError as ae:
        return False, f"AssertionError: {ae}"
    except Exception as e:
        return False, f"Execution Error ({type(e).__name__}): {str(e)}"

def run_testcase_with_timeout(code, pred_sql, sol_sql, conn, timeout):
    try:
        return func_timeout(timeout, run_testcase_sandbox, args=(code, pred_sql, sol_sql, conn))
    except FunctionTimedOut:
        return False, f"Test case execution timed out after {timeout}s."
    except Exception as e:
        return False, str(e)

def build_retry_prompt(base_prompt, fail_history, keep_last=3):
    recent_fails = fail_history[-keep_last:]
    blocks = []
    for i, h in enumerate(recent_fails, 1):
        blocks.append(f"Attempt {i}:\nError: {h['error']}\nFailed Python Code:\n```python\n{h['code']}\n```")
    
    feedback = "\n\n".join(blocks)
    retry_instruction = f"""
### Previous failed attempts
{feedback}

Please regenerate the `test_case` Python function. 
Requirements:
1. Avoid AssertionErrors or Execution Errors shown above.
2. Ensure the code properly verifies the Gold SQL logic.
3. Output ONLY the self-contained python code block.
"""
    return base_prompt + "\n\n" + retry_instruction

# 4、 会话级别（Session/Dialogue）处理
def process_single_session(item, db_dir, model, timeout, retry_k):
    """
    串行处理同一个 dialogue_id 下的多轮对话，确保状态正确继承。
    这里使用内存数据库( :memory: )加速。
    """
    if not item:
        return item

    db_id = item.get("db_id")
    dialogue = item.get("dialogue", [])
    if not db_id or not isinstance(dialogue, list) or not dialogue:
        item["testcase_status"] = "invalid_item"
        return item

    db_path = os.path.join(db_dir, db_id, f"{db_id}.sqlite")

    if not os.path.exists(db_path):
        for turn in dialogue:
            turn["testcase_status"] = "db_not_found"
            turn["testcase_code"] = ""
        item["testcase_status"] = "db_not_found"
        return item

    # 1. 载入初始数据库到内存
    disk_conn = sqlite3.connect(db_path)
    mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
    disk_conn.backup(mem_conn)
    disk_conn.close()

    # 保存状态快照 State A
    state_A_conn = sqlite3.connect(":memory:", check_same_thread=False)
    mem_conn.backup(state_A_conn)

    stop_following_turns = False
    # 同一条样本内 turn 必须串行，确保数据库状态按 turn 继承
    dialogue_sorted = sorted(dialogue, key=lambda x: x.get("turn_id", 0))

    for turn in dialogue_sorted:
        if stop_following_turns:
            turn["testcase_status"] = "skipped_due_to_previous_failure"
            turn["testcase_code"] = ""
            continue

        gold_sql = turn.get("sql", "")
        base_prompt = turn.get("testcase_prompt", "")
        turn["fail_history"] = []

        if not gold_sql.strip():
            turn["testcase_status"] = "no_sql"
            turn["testcase_code"] = ""
            continue

        if turn["sql_type"] == "SELECT":
            turn["testcase_status"] = "skipped_select"
            turn["testcase_code"] = ""
            try:
                state_A_conn.cursor().executescript(gold_sql)
                state_A_conn.commit()
            except Exception as e:
                turn["testcase_status"] = "gold_sql_execution_failed"
                # 即使 Gold SQL 执行失败，也保持当前状态尝试继续后面的轮次或直接中断均可
                stop_following_turns = True
            continue

        success = False
        final_code = ""

        for attempt in range(retry_k + 1):
            # 获取 LLM 生成的测试用例代码
            if attempt == 0:
                resp = openai_chat(base_prompt, model=model)
            else:
                retry_prompt = build_retry_prompt(base_prompt, turn["fail_history"])
                resp = openai_chat(retry_prompt, model=model)

            explanation = parse_text(resp or "")
            code = parse_python(resp or "")
            code = code.replace('\xa0', ' ')
            # code = code.encode('utf-8', 'ignore').decode('utf-8')
            if not code:
                turn["fail_history"].append({"code": "", "error": "LLM returned empty or unparseable code."})
                continue

            # 执行测试用例前，创建一个沙盒数据库(从 State A 克隆)，防止测试用例污染 State A
            sandbox_conn = sqlite3.connect(":memory:", check_same_thread=False)
            state_A_conn.backup(sandbox_conn)

            # 测试验证（在隔离的 sandbox_conn 内执行）
            gold_sql = gold_sql.replace('\xa0', ' ')
            is_pass, err_msg = run_testcase_with_timeout(
                code=code, 
                pred_sql=gold_sql, 
                sol_sql=gold_sql, 
                conn=sandbox_conn, 
                timeout=timeout
            )
            sandbox_conn.close()

            if is_pass:
                success = True
                final_code = code
                break
            else:
                turn["fail_history"].append({"code": code, "error": err_msg})

        # === 核心状态推进逻辑 ===
        # 不论测试用例成功还是历经 K 次失败，我们都需要让数据库干净地执行 gold_sql
        # 从而为下一轮 Turn (State B) 做准备，抛弃刚才测试用例可能引入的脏数据
        if success:
            turn["testcase_status"] = "success"
            turn["testcase_code"] = final_code
            turn["testcase_explanation"] = explanation
        else:
            turn["testcase_status"] = "failed"
            turn["testcase_code"] = ""
            turn["testcase_explanation"] = ""
            stop_following_turns = True
            continue

        try:
            state_A_conn.cursor().executescript(gold_sql)
            state_A_conn.commit()
        except Exception as e:
            turn["testcase_status"] = "gold_sql_execution_failed"
            # 即使 Gold SQL 执行失败，也保持当前状态尝试继续后面的轮次或直接中断均可
            stop_following_turns = True

    if not stop_following_turns:
        item["testcase_status"] = "completed"
    else:
        item["testcase_status"] = "stopped_due_to_failure"
    state_A_conn.close()

    mem_conn.close()
    return item

# Step 5: 主调度逻辑 (Session间并行)
def synthesize_testcases(db_path: str, input_file: str, output_file: str, 
                         num_cpus: int = 15, timeout: int = 120, retry_k: int = 3, model="gpt-4o"):

    with open(input_file, 'r', encoding='utf-8') as f:
        dataset = json.load(f)

    print("Building Prompts...")
    for item in tqdm(dataset, desc="Generating Prompts"):
        dialogue = item.get("dialogue", [])
        if not isinstance(dialogue, list):
            continue
        for turn in dialogue:
            build_testcase_prompt(item, turn)

    # 每条数据是一条独立多轮会话（内部 turn 串行，样本之间可并行）
    sessions = dataset
    print(f"Total isolated dialogues grouped: {len(sessions)}")

    final_dataset = [None] * len(sessions)
    # 并行处理各个相互独立的 Session
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_cpus) as executor:
        futures = {
            executor.submit(process_single_session, session, db_path, model, timeout, retry_k): i
            for i, session in enumerate(sessions)
        }
        
        for future in tqdm(concurrent.futures.as_completed(futures), total=len(futures), desc="Synthesizing & Executing"):
            processed_session = future.result()
            idx = futures[future]
            final_dataset[idx] = processed_session
            
    # 按照原数据集的顺序重新排序（防止因线程池完成顺序打乱了数据结构）
    # 假设每条数据都有原本唯一的标识符进行重组，若无唯一标识符可保留 current_session 本身的拼接
    # 这里直接按扩展的方式即可，如果需要严格复原顺序可以在外部带上 index。

    turn_status = []
    item_status = []
    for item in final_dataset:
        item_status.append(item.get("testcase_status"))
        for turn in item.get("dialogue", []):
            turn_status.append(turn.get("testcase_status"))
    success_cnt = sum(1 for s in turn_status if s == "success")
    failed_cnt = sum(1 for s in turn_status if s in {"failed", "gold_sql_execution_failed", "db_not_found"})
    complete_cnt = sum(1 for s in item_status if s == "completed")
    print(f"Synthesis Complete. Total Turns: {len(turn_status)} | Success: {success_cnt} | Failed: {failed_cnt} | Completed: {complete_cnt}")
    
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(final_dataset, f, indent=2, ensure_ascii=False)
        
    return output_file