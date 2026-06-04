PROMPT = """
You are a senior data synthesis planning expert responsible for generating an overall interaction blueprint for multi-turn Text2SQL dialogue data.

Your task is not to directly generate the final dialogue or SQL for each turn. Instead, based on the given database information, you should first design an executable, coherent, and SQL-rich overall plan for a multi-turn dialogue, which will be used later to synthesize high-quality Text2SQL data turn by turn.

### Input Information
1. Database schema information:
{{SCHEMA}}

2. Sample value information:
{{SAMPLE_VALUES}}

3. Optional SQL types:
- DML: SELECT / INSERT / UPDATE / DELETE.
- DDL: CREATE TABLE / CREATE VIEW / CREATE TRIGGER / CREATE INDEX / ALTER TABLE.

4. Dialogue turn range:
{{TURN_RANGE}}

5. SQL dialect:
{{SQL_DIALECT}}

### Goal
Please design an overall intent/plan for a multi-turn, interrelated interaction on top of this database. The plan must satisfy the following requirements:
1. The entire multi-turn dialogue should resemble a real user continuously operating on the database to accomplish one coherent business task, rather than being a collection of unrelated queries.
2. There must be explicit dependencies between turns. These dependencies may come from:
   - the query results of previous turns
   - changes in data state caused by previous INSERT / UPDATE / DELETE operations
   - schema changes caused by previous DDL operations
   - filtering conditions, entities, time ranges, or business goals mentioned earlier in the conversation
3. The plan should reflect state evolution:
   - how the data state changes over time due to DML
   - how the schema changes over time due to DDL
   - how later turns continue based on the latest database state
4. All planned operations must be compatible with the schema. Do not reference non-existent tables or columns, and do not design steps that are obviously infeasible or unreasonable.
5. The dialogue style should be close to realistic user behavior: ellipsis, reference, continuation, correction, additional constraints, and follow-up actions based on previous results are all allowed.
6. The output should be a blueprint/plan only, not final SQL, and not final natural language dialogue. Do not expand into detailed SQL for each turn.
7. When realistic and executable, the plan should include a small number of DDL operations, especially CREATE VIEW or CREATE TABLE, as part of the user's workflow rather than as isolated actions.

### Output Format
Please strictly output the following structure, operator_type should be one of {SELECT, INSERT, UPDATE, DELETE, CREATE TABLE, CREATE VIEW, CREATE TRIGGER, CREATE INDEX, ALTER TABLE}:
{{
    "Business Scenario": "",
    "User Overall Goal": "",
    "Core Tables Involved": [],
    "Key Relationships Between Tables": [],
    "Main Thread of the Multi-turn Dialogue": [
        {{
            "turn_id": 1,
            "operator_type": "",
            "plan": ""
        }},
        {{
            "turn_id": 2,
            "operator_type": "",
            "plan": ""
        }},
        ...
    ]
}}

### Special Requirements
- Return valid JSON only.
- Ensure the number of turns satisfies: {TURN_RANGE}.
- Do not generate final SQL.
- Do not generate final dialogue text."""

PROMPT2 = """You are a senior data synthesis planning expert responsible for generating an overall interaction blueprint for multi-turn Text2SQL dialogue data.

Your task is not to directly generate the final dialogue or SQL for each turn. Instead, based on the given database information, you should first design an executable, coherent, and SQL-rich overall plan for a multi-turn dialogue, which will be used later to synthesize high-quality Text2SQL data turn by turn.

### Input Information
1. Database schema information:
{{SCHEMA}}

2. Relationships between tables:
{{BUSINESS_DESC}}

3. Sample value information:
{{SAMPLE_VALUES}}

4. Optional SQL types:
- DML: SELECT / INSERT / UPDATE / DELETE, etc.
- DDL: CREATE / ALTER / DROP, etc.

5. SQL dialect:
{{db_engine}}

### Goal
Please design an overall intent/plan for a multi-turn, interrelated interaction on top of this database. The plan must satisfy the following requirements:
1. The entire multi-turn dialogue should resemble a real user continuously operating on the database to accomplish one coherent business task, rather than being a collection of unrelated queries.
2. There must be explicit dependencies between turns. These dependencies may come from:
   - the query results of previous turns
   - changes in data state caused by previous INSERT / UPDATE / DELETE operations
   - schema changes caused by previous DDL operations
   - filtering conditions, entities, time ranges, or business goals mentioned earlier in the conversation
3. The plan should reflect state evolution:
   - how the data state changes over time due to DML
   - how the schema changes over time due to DDL
   - how later turns continue based on the latest database state
4. All planned operations must be compatible with the schema. Do not reference non-existent tables or columns, and do not design steps that are obviously infeasible or unreasonable.
5. The dialogue style should be close to realistic user behavior: ellipsis, reference, continuation, correction, additional constraints, and follow-up actions based on previous results are all allowed.
6. The output should be a blueprint/plan only, not final SQL, and not final natural language dialogue. Do not expand into detailed SQL for each turn.

### Output Format
Please strictly output the following structure:
{{
    "Business Scenario": "",
    "Overall Goal": "",
    "Core Tables Involved": [],
    "Key Relationships Between Tables": [],
    "Main Thread of the Multi-turn Dialogue": [
        {{
            "turn_id": 1,
            "operator_type": "",
            "plan": ""
        }},
        {{
            "turn_id": 2,
            "operator_type": "",
            "plan": ""
        }},
        ...
    ]
}}

### Special Requirements
- Prioritize multi-turn coherence and executability first; SQL type diversity comes second.
- If the provided schema is insufficient to support a high-quality plan, provide a conservative but still executable plan.
- Return valid JSON only.
- Ensure the number of turns satisfies: {TURN_RANGE}.
- Do not generate final SQL.
- Do not generate final dialogue text."""


PROMPT2 = """You are a senior data synthesis planning expert responsible for generating an overall interaction blueprint for multi-turn Text2SQL dialogue data.

Your task is not to directly generate the final dialogue or SQL for each turn. Instead, based on the given database information, you should first design an executable, coherent, and SQL-rich overall plan for a multi-turn dialogue, which will be used later to synthesize high-quality Text2SQL data turn by turn.

### Input Information
1. Database schema information:
{{SCHEMA}}

2. Relationships between tables:
{{BUSINESS_DESC}}

3. Sample value information:
{{SAMPLE_VALUES}}

4. Optional SQL types:
- DML: SELECT / INSERT / UPDATE / DELETE, etc.
- DDL: CREATE / ALTER / DROP, etc.

5. Dialogue turn range:
{{TURN_RANGE}}

6. SQL dialect:
{{db_engine}}

### Goal
Please design an overall intent/plan for a multi-turn, interrelated interaction on top of this database. The plan must satisfy the following requirements:
1. The entire multi-turn dialogue should resemble a real user continuously operating on the database to accomplish one coherent business task, rather than being a collection of unrelated queries.
2. There must be explicit dependencies between turns. These dependencies may come from:
   - the query results of previous turns
   - changes in data state caused by previous INSERT / UPDATE / DELETE operations
   - schema changes caused by previous DDL operations
   - filtering conditions, entities, time ranges, or business goals mentioned earlier in the conversation
3. The plan should reflect state evolution:
   - how the data state changes over time due to DML
   - how the schema changes over time due to DDL
   - how later turns continue based on the latest database state
4. All planned operations must be compatible with the schema. Do not reference non-existent tables or columns, and do not design steps that are obviously infeasible or unreasonable.
5. The dialogue style should be close to realistic user behavior: ellipsis, reference, continuation, correction, additional constraints, and follow-up actions based on previous results are all allowed.
6. The plan must include both DML and DDL operations. At least one turn must use a DDL operation, and at least one turn must use a DML operation.
8. Include realistic and workflow-driven DDL operations such as CREATE VIEW or CREATE TABLE; use ALTER or DROP only when clearly justified by the business task.
9. Every DDL operation must have an explicit business purpose and must be used, referenced, or affected by at least one later turn.
10. The output should be a blueprint/plan only, not final SQL, and not final natural language dialogue. Do not expand into detailed SQL for each turn.

### Output Format
Please strictly output the following structure:
{{
    "Business Scenario": "",
    "User Overall Goal": "",
    "Core Tables Involved": [],
    "Key Relationships Between Tables": [],
    "Main Thread of the Multi-turn Dialogue": [
        {{
            "turn_id": 1,
            "operator_type": "",
            "plan": ""
        }},
        {{
            "turn_id": 2,
            "operator_type": "",
            "plan": ""
        }},
        ...
    ]
}}

### Special Requirements
- Prioritize multi-turn coherence and executability first, while ensuring meaningful SQL type diversity.
- Return valid JSON only.
- Ensure the number of turns satisfies: {{TURN_RANGE}}.
- Ensure that each turn contains exactly one SQL-related operation.
- The plan must include at least one DDL turn and at least one DML turn.
- Do not generate final SQL or final dialogue text.
"""