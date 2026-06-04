from intent_syn.get_intent import plan_synthesis
from sql_syn.sql_synthesis import sql_synthesis
from question_syn.question_synthesis import question_synthesis
from testcase_syn.testcase_synthesis import synthesize_testcases
import json
from pathlib import Path


def _get_sql_text(item):
    if "new_sql" in item and item["new_sql"]:
        return item["new_sql"]
    sql_results = item["sql_synthesis"] if "sql_synthesis" in item else None
    if isinstance(sql_results, dict):
        return sql_results["new_sql"] if "new_sql" in sql_results and sql_results["new_sql"] else ""
    return ""

def _get_question_text(item):
    question_results = item["question_results"] if "question_results" in item else None
    if isinstance(question_results, dict):
        return question_results["question"] if "question" in question_results and question_results["question"] else ""
    return ""


def _get_plan_by_turn(item, current_turn_id):
    if "description" in item and "Main Thread of the Multi-turn Dialogue" in item["description"]:
        thread = item["description"]["Main Thread of the Multi-turn Dialogue"]
    else:
        thread = []
    for turn in thread:
        if "turn_id" in turn and turn["turn_id"] == current_turn_id:
            return turn["plan"] if "plan" in turn else ""
    if isinstance(current_turn_id, int) and 1 <= current_turn_id <= len(thread):
        return thread[current_turn_id - 1]["plan"] if "plan" in thread[current_turn_id - 1] else ""
    return ""


def _build_next_turn_item(item):
    # 没有current_turn_id应该报错
    if "current_turn_id" not in item:
        return None
    current_turn_id = item["current_turn_id"]

    sql_text = _get_sql_text(item)
    question_text = _get_question_text(item)

    if not sql_text.strip() or not question_text.strip():
        return None

    plan_text = _get_plan_by_turn(item, current_turn_id)
    dialogue = item["dialogue"] if "dialogue" in item and item["dialogue"] else []
    new_mschema = item["sql_synthesis"]["executed_m_schema"] if "sql_synthesis" in item and "executed_m_schema" in item["sql_synthesis"] else ""
    new_dialogue = list(dialogue)
    new_dialogue.append(
        {
            "turn_id": current_turn_id,
            "question": question_text,
            "sql": sql_text,
            "plan": plan_text,
            "m_schema": new_mschema,
        }
    )

    next_item = {
        "db_id": item["db_id"] if "db_id" in item else None,
        "turn_range": item["turn_range"] if "turn_range" in item else None,
        "db_value_example": item["db_value_example"] if "db_value_example" in item else "",
        "m_schema": item["m_schema"] if "m_schema" in item else "",
        "description": item["description"] if "description" in item else {},
        "dialogue": new_dialogue,
        "current_turn_id": current_turn_id + 1,
    }

    return next_item


def _is_dialogue_complete(item):
    if "description" in item and "Main Thread of the Multi-turn Dialogue" in item["description"]:
        thread = item["description"]["Main Thread of the Multi-turn Dialogue"]
    else:
        thread = []
    dialogue = item["dialogue"] if "dialogue" in item and item["dialogue"] else []
    return len(thread) > 0 and len(dialogue) == len(thread)


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def run_multi_turn_synthesis(
    db_path,
    input_path,
    output_dir,
    model="gpt-4o",
    embedding_model_path="xxx/hf-models/all-mpnet-base-v2",
    max_turns=8,
):
    pending_input_path = input_path
    round_idx = 1

    successful_data = []
    failed_data = []

    for _ in range(max_turns):
        print(f"\n========== Round {round_idx} ==========")
        sql_path, synthesized_sqls, data_id = sql_synthesis(
            db_path=db_path,
            input_path=pending_input_path,
            output_dir=output_dir,
            model=model,
        )
        print(f"Synthesized SQLs saved to: {sql_path}, data_id: {data_id}")

        for item in synthesized_sqls:
            if item["sql_synthesis"]["sql_status"] != "success":
                failed_data.append(item)
            elif item["new_sql"] is None or item["new_sql"].strip() == "":
                failed_data.append(item)

        question_path, synthesized_questions, question_num2count = question_synthesis(
            db_path=db_path,
            sql_infos_path=str(sql_path),
            embedding_model_path=embedding_model_path,
            output_dir=output_dir,
            data_id=data_id,
            model=model,
        )
        
        print(f"Synthesized questions saved to: {question_path}")
        print(f"Question candidates stats: {question_num2count}")

        next_round_data = []
        cur_round_failed = []
        cur_round_success = []

        for item in synthesized_questions:
            next_item = _build_next_turn_item(item)
            if next_item is None:
                item["question_synthesis"] = {
                    "status": "failed",
                    "error": "new_sql or question or current_turn_id is empty. ",
                }
                cur_round_failed.append(item)
                failed_data.append(item)
                continue

            if _is_dialogue_complete(next_item):
                cur_round_success.append(next_item)
                successful_data.append(next_item)
            else:
                next_round_data.append(next_item)

        results_dir = Path(output_dir) / f"results_{data_id}" / "multi_turn"
        _write_json(results_dir / f"successful_turn_{round_idx}.json", cur_round_success)
        _write_json(results_dir / f"failed_turn_{round_idx}.json", cur_round_failed)
        _write_json(results_dir / f"synthetic_turn_{round_idx}.json", next_round_data)

        print(
            f"Round {round_idx} summary: success={len(cur_round_success)}, "
            f"failed={len(cur_round_failed)}, next={len(next_round_data)}"
        )

        if len(next_round_data) == 0:
            print("No pending samples. Multi-turn synthesis completed.")
            break

        pending_input_path = str(results_dir / f"synthetic_turn_{round_idx}.json")
        round_idx += 1
    
    final_results_dir = Path(output_dir) / "multi_turn"
    _write_json(final_results_dir / "successful_all.json", successful_data)
    _write_json(final_results_dir / "failed_all.json", failed_data)
    return successful_data, failed_data


if __name__ == "__main__":
    final_path, results = plan_synthesis(
        db_path="xxx/databases/bird_test",
        output_dir="./outputs_test",
        output_file = "intent_results.json",
        model="gpt-5.5",
        max_workers=10,
        turn_ranges=["3-4 turns", "5-6 turns", "7-8 turns"]
    )
    run_multi_turn_synthesis(
        db_path="xxx/databases/bird_test",
        input_path="./outputs_test/intent_results.json",
        output_dir="output_multi_turn",
        model="gpt-4o",
        embedding_model_path="xxx/hf-models/all-mpnet-base-v2",
        max_turns=8,
    )
    synthesize_testcases(
        db_path="xxx/databases/bird_test",
        input_file="xxx/multi_ambi_structure_qa/output_multi_turn/multi_turn/successful_all.json",
        output_file="xxx/multi_ambi_structure_qa/output_multi_turn/multi_turn/test_cases_sonnet46.json",
        num_cpus=20,        
        timeout=20,
        retry_k=3,
        model="claude-sonnet-4-6"
    )
