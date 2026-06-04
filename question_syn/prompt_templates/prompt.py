PROMPT = """
You are a senior Text2SQL data synthesis expert specializing in multi-turn dialogue generation.

Your task is to generate the natural-language USER question for the CURRENT turn, based on:
- the overall business scenario and overall goal,
- the historical multi-turn dialogue context,
- the database schema and sample values,
- the plan of the current turn,
- and the SQL already synthesized for the current turn.

The generated question must be the most plausible user utterance that would lead to the given CURRENT-TURN SQL in the given dialogue context.

### Input Information
1. Business scenario:
{{BUSINESS_SCENARIO}}

2. Overall goal:
{{OVERALL_GOAL}}

3. Historical dialogue context (if any):
{{DIALOGUE_CONTEXT}}

4. Database schema information:
{{SCHEMA}}

5. **Plan of the current turn**:
{{CURRENT_TURN_PLAN}}

6. SQL type of this turn:
{{SQL_TYPE}}

7. SQL dialect:
{{SQL_DIALECT}}

8. **SQL synthesized for the current turn**:
```sql
{{CURRENT_TURN_SQL}}
```

9. Style requirement for the question:
The natural language question should follow this style:
{{STYLE}}

### Goal
Generate the natural-language USER question for the current turn.

The question must satisfy all of the following:
1. It must accurately correspond to the CURRENT-TURN SQL.
2. It must be consistent with the business scenario and overall goal, and the current-turn plan.
3. It must resolve or preserve elliptical references naturally:
   - If a human user would naturally use pronouns, omissions, or follow-up wording in this context, do so.
   - If a standalone explicit question is more natural in this context, use that instead.
4. It must not mention database-internal concepts unless the dialogue context explicitly does so:
   - avoid table names, column names, joins, primary keys, foreign keys, SQL functions, or dialect-specific terms,
   - prefer business-facing wording instead of schema-facing wording.
5. It must use concrete business values when appropriate, grounded in:
   - the historical dialogue context,
   - the current-turn SQL,
   - sample values.
   Do not introduce arbitrary new literals if grounded ones already exist.
6. It must sound like a realistic user utterance in a multi-turn conversation:
   - concise when the context already establishes the target,
   - explicit when disambiguation is needed,
   - fluent, natural, and not overly formal unless the scenario suggests it.
7. It must not add requirements that are not reflected in the SQL.

### Style Requirements
- Generate exactly one user question/utterance for the CURRENT turn.
- Do not generate assistant responses or explain your reasoning.
- Do not mention SQL or schema explicitly unless the dialogue context itself does.
- Prefer natural conversational style over rigid paraphrase.
- The question may be a complete sentence or a natural follow-up utterance, depending on context.

### Output Format
[EXPLANATION-START]
<a clear and detailed explanation of the SQL query>
[EXPLANATION-END]

[QUESTION-START]
<current turn user question>
[QUESTION-END]

- **SQL Explanation**: Provide a clear and detailed explanation of the SQL query, enclosed within [EXPLANATION-START] and [EXPLANATION-END].
- **Natural Language Question**: Translate the SQL statement into a natural language request, enclosed within [QUESTION-START] and [QUESTION-END]."""


STYLE2DESC = {
   "Formal": "**Formal Style**: Uses standard grammar and vocabulary.",

   "Colloquial": "**Colloquial Style**: Employs informal vocabulary and expressions.",

   "Imperative": "**Imperative Style**: Uses command or directive sentences.",

   "Interrogative": "**Interrogative Style**: Uses question forms.",

   "Descriptive": "**Descriptive Style**: Uses detailed descriptions with contextual information.",

   "Concise": "**Concise Style**: Uses short sentences.",

   "Vague": "**Vague Style**: Includes ambiguous vocabulary requiring inference.",

   "Metaphorical": "**Metaphorical Style**: Uses metaphors or metaphorical expressions.",
}