PROMPT = """
You are an expert Database Engineer and Software Tester specialized in evaluating Text-to-SQL models.

Your task is to generate a robust, execution-based Python test case to verify the correctness of a predicted {{db_engine}} SQL statement. The test case must validate the logical correctness of the SQL by executing it and verifying the database state or returned results.

### Input Information
1. Database schema information:
{{SCHEMA}}

2. SQL type:
{{SQL_TYPE}}

3. Natural language question:
{{NATURAL_LANGUAGE_QUESTION}}

4. **SQL statement**:
```sql
{{SQL_STATEMENT}}
```

### Goal
Generate a Python function, `test_case(pred_sql, sol_sql, conn)`, that executes the predicted SQL and employs verification SQL(s) and assertions to validate its functional correctness.

### Instructions for test case generation for **{{SQL_TYPE}}**
{{SQL_TYPE_INSTRUCTION}}

### General Constraints
- If the instructions for the specific SQL type do not fully cover how to validate the correctness of the predicted SQL, you can use your expertise to design a more comprehensive test case that may go beyond the provided instructions, as long as it effectively verifies the correctness of the SQL.
- If the calculation involves floating-point numbers, you MUST use a tolerance (e.g., `abs(actual - expected) < 0.001`) for assertions.
- Keep the Python code self-contained, clean, and well-commented.
- Ensure that the test case can be executed in the database and can verify the results after executing the predicted SQL.

### Output Format
- Provide the complete Python function `test_case(pred_sql, sol_sql, conn)` as the output. The function should be ready to execute in a testing environment with the given parameters, even if `pred_sql` or `sol_sql` are not always used.
- **When it is necessary to execute SQL, use `execute_queries(sql, conn)`. You MUST use the provided `conn` parameter as the second argument for all calls to execute_queries(). The two return values of `execute_queries(sql, conn)` are the execution result and the error message, respectively.**
1. Provide a clear thought process in a text block:
```text
<Your clear thought process for how to design the test case, may including how to define verification SQL(s), how to determine expected results, and how to structure assertions>
```
2. Enclose the code of test_case in a code block:
```python
def test_case(pred_sql, sol_sql, conn):
    # 0. Execute the predicted SQL if needed to change the database state for verification. You can also execute the solution SQL if you want to compare results or states.
    
    # 1. Define the verification SQL(s) to check the current database state or logical correctness
    
    # 2. Design and execute the verification SQL to get the actual results
    
    # Optional: Assert that no execution error occurred during verification
    assert not execution_error, f"Verification query failed: \{execution_error\}"
    
    # 3. Define, extract, or calculate the expected results/state
    # May involve logic to determine what the expected outcome should be or execute additional SQL(s) to get the expected values.
    
    # 4. Assertions
    
    return 1
```"""

PROMPT_NOTYPE = """
You are an expert Database Engineer and Software Tester specialized in evaluating Text-to-SQL models.

Your task is to generate a robust, execution-based Python test case to verify the correctness of a predicted {{db_engine}} SQL statement. The test case must validate the logical correctness of the SQL by executing it and verifying the database state or returned results.

### Input Information
1. Database schema information:
{{SCHEMA}}

2. SQL type:
{{SQL_TYPE}}

3. Natural language question:
{{NATURAL_LANGUAGE_QUESTION}}

4. **SQL statement**:
```sql
{{SQL_STATEMENT}}
```

### Goal
Generate a Python function, `test_case(pred_sql, sol_sql, conn)`, that executes the predicted SQL and employs verification SQL(s) and assertions to validate its functional correctness.

### General Constraints
- If the instructions do not fully cover how to validate the correctness of the predicted SQL, you can use your expertise to design a more comprehensive test case that may go beyond the provided instructions, as long as it effectively verifies the correctness of the SQL.
- If the calculation involves floating-point numbers, you MUST use a tolerance (e.g., `abs(actual - expected) < 0.001`) for assertions.
- Keep the Python code self-contained, clean, and well-commented.
- Ensure that the test case can be executed in the database and can verify the results after executing the predicted SQL.

### Output Format
- Provide the complete Python function `test_case(pred_sql, sol_sql, conn)` as the output. The function should be ready to execute in a testing environment with the given parameters, even if `pred_sql` or `sol_sql` are not always used.
- **When it is necessary to execute SQL, use `execute_queries(sql, conn)`. You MUST use the provided `conn` parameter as the second argument for all calls to execute_queries(). The two return values of `execute_queries(sql, conn)` are the execution result and the error message, respectively.**
1. Provide a clear thought process in a text block:
```text
<Your clear thought process for how to design the test case, may including how to define verification SQL(s), how to determine expected results, and how to structure assertions>
```
2. Enclose the code of test_case in a code block:
```python
def test_case(pred_sql, sol_sql, conn):
    # 0. Execute the predicted SQL if needed to change the database state for verification. You can also execute the solution SQL if you want to compare results or states.
    
    # 1. Define the verification SQL(s) to check the current database state or logical correctness
    
    # 2. Design and execute the verification SQL to get the actual results
    
    # Optional: Assert that no execution error occurred during verification
    assert not execution_error, f"Verification query failed: \{execution_error\}"
    
    # 3. Define, extract, or calculate the expected results/state
    # May involve logic to determine what the expected outcome should be or execute additional SQL(s) to get the expected values.
    
    # 4. Assertions
    
    return 1
```"""


TYPE2INSTRUCTION = {
   "INSERT": "- Write a Verification SQL to SELECT the newly inserted record(s) using a unique identifier (like a primary key or unique business condition).\n - Assert that the record exists (e.g., len(result) > 0) and assert that the specific column values match the expected inserted data.",

   "UPDATE": "- It is typically suggested to write a Verification SQL (often a `SELECT COUNT(*)` query) rather than just checking affected rows. This query should search for dirty data — rows that met the condition but were not updated, or rows that were incorrectly modified. \n - Execute the Verification SQL and assert that the count of such erroneous rows is exactly `0`.",

   "DELETE": "- It is recommended to write a Verification SQL (such as a `SELECT COUNT(*)` query) to check if the target rows that should have been removed still exist in the database. \n - Assert that the count from the Verification SQL is `0`. If applicable, you may also want to verify that the total count of the table hasn't dropped unexpectedly to ensure unrelated rows weren't deleted.",

   "CREATE VIEW": "- It is suggested to write a Verification SQL that queries the newly created view and compares its output against a standard SQL query on the base tables with the correct logic. \n - Assert that the outputs match, using floating-point tolerance where appropriate. - DO NOT use regex (`re`) to parse view names or select bodies.",

   "CREATE TABLE": "- You can verify its existence and structure by querying system metadata like `sqlite_master` or `PRAGMA table_info()`. \n - If the table creation involves populating data (e.g., `CREATE TABLE AS` or followed by `INSERT`), it is recommended to write a Verification SQL to fetch the records from the new table and compare them with expected outcomes to ensure the data logic is correct.",

   "ALTER TABLE": "- It is suggested to use `PRAGMA table_info('table_name')` to assert that the new column was successfully added to the schema.",

   "CREATE TRIGGER": "- Perform an action (e.g., execute an `INSERT` or `UPDATE` statement) that is deliberately designed to activate the trigger's condition. \n - Write a Verification SQL to query the target table that the trigger is supposed to act (e.g., an audit log or alert table). \n - Assert that the expected side-effect (like a new log entry or value change) has successfully occurred.",

   "CREATE INDEX": "- Write a Verification SQL querying the `sqlite_master` system table (e.g., `SELECT 1 FROM sqlite_master WHERE type='index' AND tbl_name='target_table' AND name='index_name'`). \n - You can consider using the `LIKE` operator on the `sql` column in `sqlite_master` to verify that the index definition includes the specific columns or expressions required by the prompt."
}

TYPE2EXAMPLES = {
   "INSERT": "",

   "UPDATE": "",

   "DELETE": "",

   "CREATE VIEW": "",

   "CREATE TABLE": "",

   "ALTER TABLE": "",

   "CREATE TRIGGER": "",

   "CREATE INDEX": ""
}