import json
import os
import random
import sqlite3
import numpy as np
import re
import requests
import concurrent.futures
import tiktoken
import itertools
import time
import numpy as np
import math
import matplotlib.pyplot as plt
import importlib.util

from pathlib import Path
from tqdm import tqdm
from sentence_transformers import SentenceTransformer
from scipy.spatial.distance import cdist
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE
from config import API_URL, API_KEY
from question_syn.prompt_templates.prompt import PROMPT
from question_syn.prompt_templates.prompt import STYLE2DESC

seed = 42

def build_single_prompt(item, sql_dialect="SQLite"):
    description = item["description"]
    business_scenario = description["Business Scenario"].strip()
    overall_goal = description["User Overall Goal"].strip()
    normalized_thread = description["Main Thread of the Multi-turn Dialogue"]

    total_turns = len(normalized_thread)
    if total_turns == 0:
        raise ValueError("Main Thread of the Multi-turn Dialogue 为空，无法确定总 turn 数")

    current_turn_id = item.get("current_turn_id", 1)
    if current_turn_id > total_turns:
        raise ValueError(
            f"current_turn_id={current_turn_id} 超出 Main Thread 总轮数 {total_turns}"
        )

    current_turn = None
    if current_turn_id == normalized_thread[current_turn_id - 1].get("turn_id"):
        current_turn = normalized_thread[current_turn_id - 1]
    else:
        for turn in normalized_thread:
            if turn["turn_id"] == current_turn_id:
                current_turn = turn
                break
    if current_turn is None:
        raise ValueError(f"无法在 Main Thread 中定位 current_turn_id={current_turn_id}")

    current_plan = current_turn.get("plan", "")
    current_operator_type = current_turn.get("operator_type", item.get("sql_type", ""))

    dialogue = item.get("dialogue", [])
    dialogue_context = []
    for d in dialogue:
        q = d["question"]
        sql = d["sql"]
        desc = d["plan"]
        if q or sql:
            part = f"Question {d['turn_id']}: {q}\nSQL {d['turn_id']}: {sql}\nDescription {d['turn_id']}: {desc}"
            dialogue_context.append(part)

    # 随机选择一个style
    style = random.choice(list(STYLE2DESC.keys()))
    style_desc = STYLE2DESC[style]

    if dialogue and "m_schema" in dialogue[-1] and dialogue[-1]["m_schema"]:
        m_schema_str = dialogue[-1]["m_schema"]
    else:
        m_schema_str = item["m_schema"] 
    if not m_schema_str.strip():
        raise ValueError("没有可用的 m_schema 信息")

    mapping = {
        "BUSINESS_SCENARIO": business_scenario or "Not provided.",
        "OVERALL_GOAL": overall_goal or "Not provided.",
        "DIALOGUE_CONTEXT": "\n\n".join(dialogue_context) if dialogue_context else "None.",
        "SCHEMA": m_schema_str,
        "CURRENT_TURN_PLAN": current_plan,
        "SQL_TYPE": current_operator_type,
        "SQL_DIALECT": sql_dialect,
        "CURRENT_TURN_SQL": item["new_sql"],
        "STYLE": style_desc
    }

    prompt = PROMPT
    for k, v in mapping.items():
        prompt = prompt.replace(f"{{{{{k}}}}}", v)

    item["prompt"] = prompt.strip()
    if "sql" not in item:
        item["sql"] = item["new_sql"]
    return prompt, item

def load_prompts(prompt_str : str, which: str):
    prompts_json = os.path.join(prompt_str, which, "prompts.json")
    with open(prompts_json, "r", encoding="utf-8") as f:
        prompts_mod = json.load(f)
    return prompts_mod

def obtain_db_schema(db_file_dir):
    conn = sqlite3.connect(db_file_dir)
    cursor = conn.cursor()

    cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()

    table_names = []
    create_statements = []
    for table in tables:
        table_name, create_statement = table
        table_names.append(table_name)
        create_statements.append(create_statement)

    cursor.close()
    conn.close()

    return table_names, create_statements

def obtain_m_schema(db_file_dir, db_name):
    mschema_path = os.path.join(db_file_dir, db_name, f'{db_name}_mschema.txt')

    with open(mschema_path, 'r', encoding='utf-8') as f:
        mschema_str = f.read()
    return mschema_str

# Function to extract column descriptions from the CREATE TABLE statements
def extract_column_descriptions(create_statements):
    column_name2column_desc = dict()
    # Regular expression to match column definitions
    pattern = r'"(\w+)"\s+\w+\s*/\*\s*(.*?)\s*\*/'

    for create_statement in create_statements:
        # Find all matches in the string
        matches = re.findall(pattern, create_statement)

        # Print the results
        for column_name, description in matches:
            column_name = column_name.lower()
            if column_name not in column_name2column_desc:
                column_name2column_desc[column_name] = description

    return column_name2column_desc

# Function to extract foreign key relationships from m-schema
def extract_foreign_keys(mschema_str):
    foreign_keys = {}
    # Regular expression to match foreign key references
    pattern = r"(\w+)\.(\w+)=(\w+)\.(\w+)"
    matches = re.findall(pattern, mschema_str)
    for table1, column1, table2, column2 in matches:
        if table1 not in foreign_keys:
            foreign_keys[table1] = []
        foreign_keys[table1].append((column1, table2, column2))
        # print(f"Foreign Key: {table1}.{column1} -> {table2}.{column2}")
    return foreign_keys

def import_from_same_dir(module_name: str, file_name: str):
    here = Path(__file__).resolve().parent
    module_path = here / file_name  # 比如 utils.py
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod

# Function to extract relevant m-schema information
def filter_foreign_key_schema(db_path, db_id, mschema_str, foreign_keys, target_tables):
    relevant_schema = []
    relevant_schema.append("【Schema】")
    fk_schema = []

    for target_table in target_tables:
        # print("Processing table: ", target_table)
        # pattern = re.escape(target_table) + r"\s*\[\s(\(.*?\))\s\]"
        # matches = re.findall(pattern, mschema_str, re.DOTALL)
        # # print("mschema_str: ", mschema_str)
        
        # for match in matches:
        #     relevant_schema.append(f"# Table: {target_table}")
        #     # print(f"Match for table {target_table}: ", match)
        #     columns = match.split('\n')
        #     relevant_schema.append("[")
        #     for column in columns:
        #         relevant_schema.append(column.strip())
        #     relevant_schema.append("]")
        with open(os.path.join(db_path, db_id, f"{db_id}_mschema.json"), 'r', encoding='utf-8') as f:
            mschema_json = json.load(f)
        for tbl in mschema_json['tables']:
            if '.' + target_table in tbl:
                table_info = mschema_json['tables'][tbl]
                relevant_schema.append(f"# Table: {target_table}")
                relevant_schema.append("[")
                
                # 处理表中的每一个字段
                example_num = 5
                # from utils import examples_to_str
                utils = import_from_same_dir("utils_local", "utils.py")
                examples_to_str = utils.examples_to_str
                for field_name, field_info in table_info['fields'].items():

                    raw_type = field_info['type'].split("(")[0]
                    field_line = f"({field_name}:{raw_type.upper()}"
                    if field_info['comment'] != '':
                        field_line += f", {field_info['comment'].strip()}"
                    else:
                        pass

                    ## 打上主键标识
                    is_primary_key = field_info.get('primary_key', False)
                    if is_primary_key:
                        field_line += f", Primary Key"

                    # 如果有示例，添加上
                    if len(field_info.get('examples', [])) > 0 and example_num > 0:
                        examples = field_info['examples']
                        examples = [s for s in examples if s is not None]
                        examples = examples_to_str(examples)
                        if len(examples) > example_num:
                            examples = examples[:example_num]

                        if raw_type in ['DATE', 'TIME', 'DATETIME', 'TIMESTAMP']:
                            examples = [examples[0]]
                        elif len(examples) > 0 and max([len(s) for s in examples]) > 20:
                            if max([len(s) for s in examples]) > 50:
                                examples = []
                            else:
                                examples = [examples[0]]
                        else:
                            pass
                        if len(examples) > 0:
                            example_str = ', '.join([str(example) for example in examples])
                            field_line += f", Examples: [{example_str}]"
                        else:
                            pass
                    else:
                        field_line += ""
                    field_line += ")"

                    relevant_schema.append(field_line)
        
                relevant_schema.append("]")

        for fk_table, fk_info in foreign_keys.items():
            if fk_table == target_table:
                for fk_column, target_table_name, target_column in fk_info:
                    if target_table_name in target_tables:
                        fk_schema.append(f"{fk_table}:{fk_column}={target_table_name}:{target_column}")
    
    relevant_schema.append("【Foreign keys】")
    relevant_schema.extend(fk_schema)
    schema_str = "\n".join(relevant_schema)
    # print(f"Relevant Schema for tables {target_tables}:\n {schema_str}")
    # print("---------------------------------------------------")
    return relevant_schema

# step1: generate prompts
def generate_prompt(db_path: str, sql_infos_path: str, output_dir: str, output_file: str, data_id : str):
    random.seed(seed)
    sql_infos = json.load(open(sql_infos_path, "r", encoding="utf-8"))
    print(f"Loaded {len(sql_infos)} SQL synthesis results from {sql_infos_path}")

    prompt_data = []
    for sql_info in tqdm(sql_infos):
        try:
            if sql_info["sql_synthesis"]["sql_status"] == "success":
                if sql_info["new_sql"] is not None and sql_info["new_sql"].strip() != "":
                    _, new_item = build_single_prompt(sql_info)
                    prompt_data.append(new_item)
                else:
                    sql_info["question_synthesis"] = {
                        "question_status": "failed",
                        "error": "SQL synthesis succeeded but new_sql is empty."
                    }
        except Exception as e:
            print(f"Error processing item with db_id={sql_info['db_id']}, error: {e}")
            sql_info["prompt_error"] = str(e)
            sql_info["question_synthesis"] = {
                "question_status": "failed",
                "error": str(e)
            }

    out_dir = Path(output_dir)
    out_path = out_dir / f"results_{data_id}" / output_file
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(prompt_data, indent=2, ensure_ascii=False))

    return out_path, prompt_data



api_url = API_URL
api_key = API_KEY

def parse_response(response):
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

def fetch_n_responses(prompt, model, n):
    """
    针对单个 prompt，循环调用 openai_chat n 次，获取 n 个结果。
    """
    answers = []
    for _ in range(n):
        res = openai_chat(prompt, model=model)
        if res:
            answers.append(res if isinstance(res, str) else res[0])
        else:
            answers.append(None)
    return answers

def llm_inference(model, input_datas, max_workers=50, num_responses=3):
    """
    Generates responses using an LLM for given prompts.

    Args:
        model: The LLM to use for generating responses.
        prompts (list of str): A list of prompts for the model.
        db_ids (list of str): A list of database IDs corresponding to each prompt.

    Returns:
        list of dict: A list of dictionaries containing the prompt, db_id, and generated response.
    """
    
    # Replace with actual LLM call to generate responses
    # `responses` should be a list of strings (list of str), where each string is the LLM's output for a prompt.
    # responses = None # model.generate(prompts, temperature=0.8, n=1), this is an example call, adjust as needed
    prompts = [item["prompt"] for item in input_datas]
    for i, p in enumerate(prompts):
        n_tokens = count_tokens(p, model)
        token_limit_for_prompt = 89600
        if n_tokens > token_limit_for_prompt:
            cur_db_id = input_datas[i].get("db_id", "")
            print(
                f"[超过限制] idx={i}, db_id={cur_db_id}, tokens={n_tokens} > {token_limit_for_prompt}"
            )
    
    flat_tasks = []
    for idx, item in enumerate(input_datas):
        for _ in range(num_responses):
            flat_tasks.append((idx, item["prompt"]))

    # 2. 全量并发请求
    results_map = {i: [] for i in range(len(input_datas))}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 这里的 p[1] 是 prompt
        future_to_idx = {executor.submit(openai_chat, p[1], model): p[0] for p in flat_tasks}
        
        for future in tqdm(concurrent.futures.as_completed(future_to_idx), total=len(flat_tasks)):
            orig_idx = future_to_idx[future]
            try:
                res = future.result()
                # openai_chat returns List[str]; flatten each candidate as a separate response
                if isinstance(res, list):
                    for r in res:
                        results_map[orig_idx].append(r)
                else:
                    results_map[orig_idx].append(res)
            except Exception:
                results_map[orig_idx].append(None)

    print(f"len(prompts): {len(prompts)}")
    print(f"Generated {len(results_map)} responses.")
    assert len(prompts) == len(results_map)

    for i, item in enumerate(input_datas):
        item["responses"] = results_map[i]

    return input_datas

# step2: synthesize questions
def synthesize_question(model: str, input_file: str , output_dir: str, output_file: str, data_id: str, num_responses: int = 3):
    input_dataset = json.load(open(input_file, "r", encoding="utf-8"))
    results = llm_inference(model, input_dataset, num_responses)

    for idx, result in enumerate(results):
        result["question_results"] = []
        for response in result["responses"]:
            question_result = parse_llm_response(response)
            result["question_results"].append(question_result)

    out_path = Path(output_dir) / f"results_{data_id}" / output_file
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(results, indent=2, ensure_ascii=False))

    return out_path, results



def visualize_embeddings(embeddings, min_index):
    pca = PCA(n_components=2)
    embeddings_2d = pca.fit_transform(embeddings)

    plt.figure(figsize=(8, 6))

    plt.scatter(embeddings_2d[:, 0], embeddings_2d[:, 1], color='red', label='Other Points')
    plt.scatter(embeddings_2d[min_index, 0], embeddings_2d[min_index, 1], color='blue', label='Central Point', s=100)

    plt.legend()

    plt.title('2D PCA of Embeddings')
    plt.xlabel('PCA Component 1')
    plt.ylabel('PCA Component 2')

    plt.savefig(f"embeddings/figure-{random.randint(0,10000000000)}")

def parse_llm_response(response):
    explanation_pattern = re.compile(r'\[EXPLANATION-START\](.*?)\[EXPLANATION-END\]', re.DOTALL)
    question_pattern = re.compile(r'\[QUESTION-START\](.*?)\[QUESTION-END\]', re.DOTALL)
    external_knowledge_pattern = re.compile(r'\[EXTERNAL-KNOWLEDGE-START\](.*?)\[EXTERNAL-KNOWLEDGE-END\]', re.DOTALL)

    explanation_match = explanation_pattern.search(response)
    question_match = question_pattern.search(response)
    external_knowledge_match = external_knowledge_pattern.search(response)

    explanation_content = explanation_match.group(1).strip() if explanation_match else ""
    question_content = question_match.group(1).strip() if question_match else ""
    external_knowledge_content = external_knowledge_match.group(1).strip() if external_knowledge_match else ""
    
    if explanation_content == "" or question_content == "":
        return None
    else:
        return {
            "question": question_content.strip(),
            "explanation": explanation_content.strip(),
            # "external_knowledge": external_knowledge_content.strip()
        }
    
def integrate_info(sql2question_prompt_info, question_info):
    if sql2question_prompt_info["db_id"].endswith(".db"):
        db_id = sql2question_prompt_info["db_id"][:-3]
    else:
        db_id = sql2question_prompt_info["db_id"]
    return {
        "db_id": db_id,
        "sql": sql2question_prompt_info.get("sql", sql2question_prompt_info.get("new_sql", "")),
        "sql_explanation": question_info["explanation"],
        "question": question_info["question"],
        "external_knowledge": question_info["external_knowledge"]
    }

def edu_distance(vector1, vector2):
    distance = 0
    for num1, num2 in zip(vector1, vector2):
        distance += (num1-num2) ** 2
    return math.sqrt(distance)

# step3: postprocess questions
def postprocess_question(input_file: str, output_dir: str, output_file: str, data_id: str,
                         embedding_model_path: str = "/root/huggingface_models/embedding/all-mpnet-base-v2",
                         device: str = "cuda:0", do_visualize: bool = False):
    random.seed(seed)
    input_dataset = json.load(open(input_file, "r", encoding="utf-8"))

    print("loading SentenceTransformer....")
    embedding_model = SentenceTransformer(model_name_or_path=embedding_model_path, device=device)

    valid_questions_num = []
    result_dataset = []

    for data in tqdm(input_dataset):
        question_infos = []
        # for response in data["responses"]:
        #     question_info = parse_llm_response(response)
        #     print("parsed question_info:", question_info)
        #     if question_info is not None:
        #         question_infos.append(question_info)
        question_infos = data["question_results"]

        valid_questions_num.append(len(question_infos))

        if len(question_infos) == 0:
            continue
        elif len(question_infos) == 1:
            data["question_results"] = question_infos[0]
        elif len(question_infos) == 2:
            data["question_results"] = random.sample(question_infos, 1)[0]
        else:
            # texts = [(qi["external_knowledge"] + " " + qi["question"]).strip() for qi in question_infos]
            texts = [qi["question"].strip() for qi in question_infos]
            embeddings = embedding_model.encode(texts)

            distance_matrix = cdist(embeddings, embeddings, metric="cosine")
            distance_sums = distance_matrix.sum(axis=1)
            min_index = int(np.argmin(distance_sums))

            # chosen = integrate_info(data, question_infos[min_index])
            # result_dataset.append(chosen)
            data["question_results"] = question_infos[min_index]

            # print("EK:\n", data["question_results"]["external_knowledge"])
            print("Question:\n", data["question_results"]["question"])
            print("SQL:\n", data["new_sql"])
            print("---------------------------------------")
            if do_visualize:
                visualize_embeddings(embeddings, min_index)

    out_path = Path(output_dir) / f"results_{data_id}" / output_file
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(input_dataset, indent=2, ensure_ascii=False))

    question_num2count = {}
    for num in valid_questions_num:
        question_num2count[num] = question_num2count.get(num, 0) + 1
    print(question_num2count)

    return out_path, input_dataset, question_num2count


def question_synthesis(db_path: str, sql_infos_path: str, output_dir: str, 
                        data_id: str, model: str = "gpt-4o",
                        embedding_model_path: str = "/root/huggingface_models/embedding/all-mpnet-base-v2",
                        device: str = "cuda:0", do_visualize: bool = False, num_responses: int = 3):
    # step1: generate prompts
    prompt_out_path, sql_infos_with_prompts = generate_prompt(db_path=db_path, sql_infos_path=sql_infos_path,
                                                             output_dir=output_dir,
                                                             output_file="question_synthesis_prompts.json",
                                                             data_id=data_id)
    print(f"Prompts saved to: {prompt_out_path}")

    # step2: synthesize questions
    llm_out_path, llm_results = synthesize_question(model=model,
                                                    input_file=prompt_out_path,
                                                    output_dir=output_dir,
                                                    output_file="question_synthesis_results.json",
                                                    data_id=data_id,
                                                    num_responses=num_responses)
    print(f"LLM results saved to: {llm_out_path}")

    # step3: postprocess questions
    final_out_path, final_dataset, question_num2count = postprocess_question(input_file=llm_out_path,
                                                                             output_dir=output_dir,
                                                                             output_file="question_and_sql_pairs.json",
                                                                             data_id=data_id,
                                                                             embedding_model_path=embedding_model_path,
                                                                             device=device,
                                                                             do_visualize=do_visualize)
    print(f"Final question and SQL pairs saved to: {final_out_path}")

    return final_out_path, final_dataset, question_num2count