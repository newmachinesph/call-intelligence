# Rubric configuration

`example-v1.json` is a generic example: three rubrics (`sales`, `office` for customer-service calls, and `retention`), each a list of one-point criteria with a plain-language `stage_definition` the evaluator reads, and gold / green / below-standard thresholds. Replace it with your own scoring guide before relying on grades.

Rules the code enforces (`call_intelligence_api/scoring.py`): every criterion is worth one point, `maximum_points` equals the criterion count, IDs are unique, and automatic-credit rules only reference criteria in the same rubric. The example's one automatic rule credits the three objection-handling criteria on a sales call whose complete transcript shows no objection.

Criterion IDs are shown in the app as labels (`sales.needs_summary` becomes "Needs summary"), so choose readable IDs. Change scoring policy only through a new versioned file; `version` is stored on every analysis run.

To give the evaluator more detail than the definitions, put an optional plain-text guide per rubric in `MANUAL_TEXT_DIR` named `<rubric id>-guide.txt`, for example `sales-guide.txt`.
