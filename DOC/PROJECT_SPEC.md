# PROJECT SPEC: Script-to-Storyboard MVP (Track 02)

## 1. Goal
Upload an approved Shot Planner and get consistent character and location references, one storyboard per scene, AI-scored panels, human approvals, one 16:9 final image per shot, and a full token and cost log.

**Success means:** characters stay on model, panels match the plan, cost per approved image is low, the sample plan works end to end, and fixing one panel is quick.

**Deadline:** submit on or before **Sunday 29 Nov 2026, 6 PM IST**.

**Priority:** quality first, then cost.
- Approved references and reactions are attached to every relevant image request.
- The look is fixed on Scene 1 before other scenes.
- Every panel is reviewed; weak panels are fixed one at a time.
- Finals use high quality by default.

## 2. Flow and gates

| # | Step | Gate to pass before the next step |
|---|------|-----------------------------------|
| 1 | Upload Shot Planner (PDF), parse to `plan.json` | Creator checks the parsed list |
| 2 | Library: character names, references and reactions, plus location references (upload or generate) | Creator approves **each** reference and reaction |
| 3 | Storyboard: one draft panel per shot, composed into a scene board | All panels generated |
| 4 | AI review: score every panel | Review completed for every panel |
| 5 | Human review: approve board, or fix one panel with a one-line comment | Creator approves **each scene board** |
| 6 | Final render: one image per approved shot, shown as soon as ready | Creator approves **each final** |
| 7 | Export outputs and usage log | |

Rules:
- The server enforces every gate. Hiding a button is not enough.
- Scene 1 is boarded and approved first, to fix the look before other scenes.
- AI review advises; the human decides. A failed score is highlighted, and approving it needs an explicit override.
- A fixed panel is regenerated alone, reviewed again, and the board is recomposed in code. The board needs approval again.
- Rejecting a reference or final takes one short comment, which is applied on regeneration.
- If an approved reference changes, every panel and final that uses it is marked stale.
- Automatic retries are for failed calls or invalid output only, maximum 2, then hand to the person. Low scores never trigger paid regeneration without a click.

## 3. Dynamic input
- No story, character, location or shot count is hard-coded.
- Each character and location offers **Upload** (used instead of generating) or **Generate** (from the planner description, with an optional comment).
- References show one subject, with no text labels.
- Character names come from the planner (editable) and link each shot to the right images.
- **Reactions:** after a character's base reference is approved, add one image per emotion that character needs in the planner (upload or generate from the base reference).
- A panel request gets each character's base reference and matching reaction, plus the location reference.

## 4. Data model (`plan.json`)
```json
{
  "title": "",
  "style": "",
  "characters": [{"id": "", "name": "", "description": "", "reactions": []}],
  "locations":  [{"id": "", "name": "", "description": ""}],
  "scenes": [{"id": "S01", "shots": [{
    "id": "S01_SH01", "location_id": "", "character_ids": [],
    "action": "", "dialogue": "", "reactions": [{"character_id": "", "emotion": ""}], "framing": "",
    "time_of_day": "", "props": []
  }]}]
}
```
Adjust the fields after checking the real Shot Planner.

### Storage modes
One setting in `.env` picks where everything is stored.

| `STORAGE_MODE` | Data | Images |
|----------------|------|--------|
| `local` (default) | JSON files in `data/` | Local folders |
| `cloud` | Database, same records stored as JSON | Cloudflare R2 |

```
STORAGE_MODE=local          # local | cloud
OPENAI_API_KEY=

# cloud mode only
DATABASE_URL=
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=
```

- The app calls one storage interface (`get/save record`, `put/get image`) with a local and a cloud version. No other code depends on the mode.
- Records hold an image key such as `library/characters/theo.png`, the same in both modes. Each version gets its own file so earlier ones are kept.
- Proposed database: PostgreSQL with `JSONB` columns. R2 is accessed through its S3-compatible API (`boto3`).
- Keep the R2 bucket private; show images through the app or short-lived links.
- On startup, check the settings for the chosen mode and stop with a clear message if any are missing. Never switch modes silently.
- Switching modes does not move existing data.
- Export writes the `output/` folder to local disk in both modes, for submission.

### Local JSON files

| File | Holds |
|------|-------|
| `data/plan.json` | Parsed planner |
| `data/library.json` | Characters, reactions, locations: path, status, comment, version |
| `data/storyboards.json` | Panels and boards: path, status, comment, version |
| `data/reviews.json` | Scores, notes and verdict per panel version, human decision |
| `data/finals.json` | Final images: path, status, comment, version |
| `data/usage_log.json` | One record per model call |

In local mode, one storage module does all writes: lock, write a temporary file, then rename, so parallel jobs do not corrupt or overwrite data. Validate records on load.

Status per item: `pending → generating → in_review → needs_approval → approved | rejected | stale`

## 5. Prompts as Markdown
```
prompts/
  parse_plan.md
  character_ref.md
  location_ref.md
  panel.md
  panel_fix.md
  final.md
  review.md        <- owned by the review teammate
```
Markdown keeps prompts editable and versioned. It does **not** by itself lower cost: the file's text is billed as tokens. Cost control comes from:
- Short prompts, sending only the current shot's data
- Static instructions first and shot data last, so repeated prefixes can be cached (OpenAI needs at least 1,024 tokens; check the cache-write charge for the chosen model)
- Draft quality for panels, final quality only for approved shots
- Single-panel fixes, and reusing saved results
- Downscaled images for review, as long as review accuracy holds

## 6. AI review contract
**Input:** `review.md`, the shot's plan data, the panel, the approved references, and the previous and next panels.

**Output (strict JSON, validated):**
```json
{
  "shot_id": "S03_SH03",
  "scores": {"character": 0, "background": 0, "emotion": 0, "continuity": 0, "framing": 0},
  "overall": 0,
  "verdict": "pass | fail",
  "notes": "short note",
  "suggested_fix": "one line"
}
```

**Placeholder scoring** (replace with the teammate's rules). Each score is 0 to 10. The app, not the model, computes `overall` (0 to 100) and `verdict`.

| Criterion | Weight |
|-----------|--------|
| Character (right people, face, hair, clothes) | 35 |
| Background / location | 20 |
| Emotion / expression | 20 |
| Continuity with neighbouring panels | 15 |
| Framing (required by the brief) | 10 |

Pass: `overall >= 75` and `character >= 7`. Keep weights and thresholds in `config.yaml`.

If neighbours change after a fix, re-check their continuity.

## 7. Models and quality
| Use | Model | Setting |
|-----|-------|---------|
| Images | `gpt-image-2.6-sunburst` | draft = low, final = high (creator can switch) |
| Parsing, prompts | OpenAI text model allowed by the team key | in `config.yaml` |
| Review | OpenAI vision model allowed by the team key | in `config.yaml` |

- Attach the approved references to **every** image request.
- A final uses the approved panel plus the library references.
- Panels are 16:9. Confirm the supported pixel sizes in the API docs.
- Captions (shot ID, action, dialogue) are drawn by code, not by the image model.

## 8. Tokens and cost
Log **every** call, including failures and retries that return usage.

Stored in `data/usage_log.json` and exported to `output/usage_log.csv` for submission: `time, shot_id, step, model, input_tokens, output_tokens, images, gpu_seconds, cost_usd`

- Read token counts from the API response. Never estimate when usage is returned.
- Cost = tokens × rate, with text, image and cached tokens priced separately.
- Image model rates per 1M tokens (OpenAI pricing page, checked 27 Nov 2026): text input $5.00 (cached $1.25), image input $8.00 (cached $2.00), image output $30.00. Confirm whether cached rates apply to the endpoint used.
- Add text and vision model rates to `config.yaml`.
- Library calls use `shot_id = LIB:<name>`; their cost is shared across shots in the report.
- **UI shows:** tokens and cost per call, per shot, per step, total, cost per approved final (total cost / approved finals), and remaining budget from $50.
- Warn at 80% of budget; block new calls at the limit.

## 9. Screens
1. Upload and parsed plan
2. Library (upload, generate, approve, reject with comment)
3. Storyboard (panels, scores, notes, fix one panel, approve board)
4. Finals (live progress, approve, reject)
5. Usage and cost

## 10. Suggested stack and structure
Python, Streamlit (proposed), OpenAI SDK, Pillow. Cloud mode adds PostgreSQL and `boto3`.
```
app.py   config.yaml   .env.example   README.md
core/    parser, library, storyboard, review, finals, gates, storage/ (base, local, cloud), llm (all calls + logging)
data/    JSON files
prompts/
input/
output/  library/characters/  library/locations/  storyboards/  keyframes/  reviews/  usage_log.csv
```
File names: `theo.png`, `hillside-pasture.png`, `S03_storyboard.png`, `S03_SH03.png`.

All keys live in `.env`, which is in `.gitignore`. `.env.example` lists the names with empty values.

## 11. Build schedule (IST)

| By | Done |
|----|------|
| Fri 27 Nov, night | Skeleton, JSON storage, call logging, budget guard, planner parser |
| Sat 28 Nov, 1 PM | Library: upload, generate, reactions, approvals |
| Sat 28 Nov, night | Panels, board composer, review, single-panel fix |
| Sun 29 Nov, 10 AM | Finals, cost screen, CSV export |
| Sun 29 Nov, noon | Cloud mode (database + R2), only if local mode works end to end |
| Sun 29 Nov, 2 PM | Full run on the sample, quality fixes, feature freeze |
| Sun 29 Nov, 4 PM | README and demo video (3 minutes or less) |
| Sun 29 Nov, 6 PM | Submitted (4 to 6 PM includes buffer) |

If the teammate's `review.md` is not ready by Saturday evening, build with the placeholder and swap it in later.

## 12. Submission checklist
- [ ] Code repository with README (how to run, models used, where keys go)
- [ ] Demo video
- [ ] Outputs from the sample plan, including review notes per panel
- [ ] Usage log CSV with total cost per shot

## 13. Later (bonus)
Full expression sheets beyond the planner's emotions; cross-scene continuity for clothes, time of day and props.

## 14. Open items
- Sample Shot Planner PDF, sample `output/` folder and `usage-log_example.csv`
- Teammate's `review.md`, scoring weights and pass mark
- Allowed text and vision model names
- Confirm Python + Streamlit
- Confirm the database (PostgreSQL proposed) and create the R2 bucket
- The brief says Sunday 9 PM; we are working to 6 PM
