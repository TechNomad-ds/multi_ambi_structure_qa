PROMPT = """
You are a senior Text2SQL SQL synthesis expert.

Your task is to generate the executable SQL for the CURRENT turn, based on:
- the overall business scenario and overall goal,
- the historical multi-turn dialogue context,
- the database schema and sample values,
- and the plan of the current turn.

You need to produce the final SQL that best realizes the current-turn plan, while remaining meaningfully connected to previous turns and reasonably filling in necessary details.

### Input Information
1. Business scenario:
{{BUSINESS_SCENARIO}}

2. Overall goal:
{{OVERALL_GOAL}}

3. Historical dialogue context (if any):
{{DIALOGUE_CONTEXT}}

4. Database schema information:
{{SCHEMA}}

5. Sample value information:
{{SAMPLE_VALUES}}

6. **Plan of the current turn**: 
{{CURRENT_TURN_PLAN}}

7. SQL type of this turn:
{{SQL_TYPE}}

8. SQL dialect:
{{SQL_DIALECT}}

### Goal
Generate the SQL for the current turn.

The SQL must satisfy all of the following:
1. It must accurately implement the current-turn plan and be consistent with the business scenario and overall goal.
2. It must correctly inherit the dialogue state from history, including:
   - entities, filters, constraints, or target records mentioned earlier.
   - the latest data state after previous INSERT / UPDATE / DELETE operations.
   - the latest schema state after previous CREATE / ALTER / DROP operations.
3. It must resolve elliptical references in the current-turn plan using the historical context.
4. It must only use tables, columns, and objects that exist in the schema.
5. It must be executable in the given SQL dialect.
6. **It must use concrete values when needed. Prefer values grounded in:
   - historical dialogue context,
   - previous SQL,
   - previous results if provided,
   - sample values,
   rather than inventing arbitrary literals.**
7. It should reflect realistic SQL authoring choices, including but not limited to:
   - using precise WHERE conditions for UPDATE / DELETE,
   - selecting only the columns needed by the current turn,
   - considering type conversion when necessary,
   - handling boundary cases reasonably and avoiding divide-by-zero errors when relevant,
   - avoiding duplicate insertion when the plan involves INSERT or data creation.
8. If details are partially underspecified, make the reasonable choice that is most consistent with the scenario, dialogue history, and current plan.

### Output Format
Provide a clear thought process in a text block:
```text
<Your clear thought process for how to synthesize the SQL, including how to resolve elliptical references, how to fill in underspecified details, how to ensure consistency with the dialogue history and current plan>
```
Enclose the SQL query in a code block:
```sql
  -- Your SQL query here
```

### Special Requirements
- Do not generate the question for this turn or generate natural-language explanation.
- Do not generate multiple candidate SQLs.
- Do not invent nonexistent tables, columns, or foreign-key relationships.
- Do not ignore history: later turns must remain meaningfully connected to previous turns.
"""