# Consciously-accepted coverage gaps

> **Question this document answers:** where in this repo we deliberately do **not** test, and
> why — so that a rejected-as-negative-ROI decision is not silently re-opened as work-for-work
> (goal-function priority (2)). Strategy — levels, taxonomy, what we mock — is
> [`testing.md`](testing.md); this file is the case-by-case ledger it refers to.
>
> Records carry stable letter IDs (`A`…`AB`); a state doc links to the letter instead of
> retelling the reasoning next to itself.

Every bug category in the [taxonomy](testing.md#bug-taxonomy) is covered by tests today (navigate to
them with `grep` by module/feature name — there is no hand-curated per-category index, it
only drifts).

**What belongs here:** "we didn't cover X with a test". The other branches of the "which home a
decision goes to" route — and the rule itself — live in
[`project-map.md`](project-map.md) §Canonical-home.

**Rejected as negative-ROI (a test would only ever guard CI minutes, not correctness):**

- **A. Structure drift — no *live* E2E for GitHub `new_popular` / Steam JSON.** Integration
  tests cover parsing with saved fixtures; the daily cron is the E2E smoke (zero-row drift →
  red CI next run). A dedicated live-E2E was rejected per the «cron = E2E smoke» doctrine
  ([Test levels](testing.md#test-levels)). Live E2E *does* exist where structure drift is silent and
  frequent: `test_e2e_kinozal_titles.py`, `test_e2e_github_trending.py`.
- **C. Auth & quota — GitHub 401 not tested.** The token rarely 401s and a downstream
  zero-row → red CI catches the outage; a dedicated 401 guard is negative-ROI.
- **K. Sheets 5xx retry — dup-write / already-exists races not tested (#288).** Broadening
  `SheetsStorage` retry from 429-only to transient 5xx (500/502/503/504) raised the odds of a
  `append_rows` **duplicate row** on retry: a 5xx that lands *after* the batch partially wrote
  re-appends on the next attempt (429 usually rejects *before* writing, so this is genuinely
  newer/likelier than the prior behaviour). **Accepted** — next-run read-dedup (`get_existing_keys`)
  drops the dup; a test would need live/ambiguous-timing conditions to reproduce (§V documented
  mitigation, not silent). Same class on `add_worksheet` (5xx after server-side create → retry
  hits a non-transient "already exists" 4xx → aborts, *doesn't* self-heal) — rarer still (once
  per tab, ever) and left untested for the same reason. Behaviour is correct; only the timing
  race is uncovered, recorded here so it isn't re-litigated as work-for-work.
- **L. `fetch_bytes` image-`Accept` header / impersonate-profile merge — verified live only (#296).**
  The fix makes `fetch_bytes` send an `<img>`-style `Accept: image/*` so content-negotiating hosts
  (imageban.ru, fastpic) serve the JPEG instead of an HTML landing page. The unit test
  (`test_sends_image_accept_header`) asserts the header is *passed* to `requests.get`, but **cannot**
  observe curl_cffi's real behaviour: that `headers=` merges by key over the impersonate profile
  (so UA / Sec-Ch-Ua / TLS fingerprint — the #217/#225 403-avoidance — survive) and that the target
  host actually returns image bytes. Both were verified live against imageban/fastpic + a
  header-echo endpoint; the standing gate is the daily cron E2E (a fingerprint regression → 403 on
  posters → §IV-visible next run), same «cron = E2E smoke» doctrine as **A**. Recorded so the
  live-only verification isn't re-opened as a mock-the-network work-for-work test.
- **M. `http_fetch` retry deliberately scoped to HTTP-status errors only (#306).** The retry layer
  (`_retry_transient_http`) fires on transient HTTP *responses* (403/429/5xx) but **not** on network
  errors (`Timeout` / `ConnectionError` — curl_cffi `RequestException` subclasses that never reach
  `raise_for_status`, so the `isinstance(HTTPError)` predicate skips them by construction). **Accepted**
  — no reproduced incident (§V: don't retry what wasn't observed), symmetric with the `SheetsStorage`
  sibling which covers `APIError` status only. The asymmetry «503 retries, a DNS blip crashes the source»
  is real and conscious; a broadening waits for an actual network-error incident. Separately, the raw
  `requests.get` calls in `github_popular_pipeline.py` (GitHub API) and `steam_pipeline.py` still have
  **no** retry — a different transport (stdlib `requests`, not curl_cffi via `http_fetch`) — deferred to
  a follow-up issue so `_retry_transient_http` can be reused there. Recorded so neither is re-opened as
  work-for-work.

- **N. LLM / embedding / TMDB trailer-picker strategies built but deliberately NOT in the prod
  hot path (#144/#315).** Прод `enrich_with_trailer` отбирает детерминированным `HeuristicStrategy`
  (#141); `LLMTrailerStrategy` (#142), `EmbeddingTrailerStrategy` (#143) и `tmdb_trailer.pick_trailer`
  (#329) остаются eval-only. **Обоснование выбора (negative-ROI, wrong=0 на golden-set) — канон в
  [pipeline.md § Trailer retrieval and selection](pipeline.md#trailer-retrieval-and-selection-140-141-144)**,
  здесь не дублируем. Coverage-следствие (дом здесь): чистые selection-слои этих стратегий **покрыты**
  unit-тестами; без покрытия только живые Gemini-движки (строки ниже). Записано, чтобы «почему
  LLM-picker не в проде?» не переоткрывали. **Open-world caveat:** wrong=0 доказан на 28
  curated-кейсах; success-path breadcrumb (`reason`/`confidence` INFO-лог в `enrich_with_trailer`)
  вскроет прод-ambiguity — пересмотреть, если в проде всплывут ничьи, которых нет в golden-set.
  **Ревизит состоялся (#359, 2026-07-24) и дал обратный результат — записано, чтобы вывод не
  переоткрывали.** Breadcrumb сработал: в run `30066249488` 5 из 6 picks оказались
  `ambiguous (conf=0.3)`. Гипотеза «ничья → произвольный выбор → чужая ссылка» была реализована
  (подавление picks с `confidence < 0.5` в miss-маркер) и **откачена по замеру**: на 28 golden-
  кейсах 26 hit → 16, 2 miss → 12, wrong 0 → 0. Все 10 подавленных picks были **попаданиями** —
  `confidence=0.3` означает «несколько одинаково хороших трейлеров одного фильма» (дубляж №1 vs
  №2), ровно то, что и моделируют accept-set'ы. Прод-ничьи частые, но безвредные; #377 (каст как
  разрыватель ничьих) закрыт как wontfix. Golden-запись по «Суете» не добавлена: верифицируемо-
  неверного кандидата в захваченном пуле нет (все 5 — трейлеры того же сериала), а догадка в
  эталоне отравила бы eval. #359 в итоге сузился до диагностики: `video_id` в breadcrumb.
  **Остаточный пробел закрыт (#380, 2026-07-27):** в наборе появились кейсы с верифицированным
  чужим кандидатом (`trap`, блок «Отрицательный полюс метрики» выше), `wrong` больше не 0. Замер
  #359 по обновлённому набору вывод не изменил, а усилил: политика не трогает `wrong` вовсе
  (реальный чужой pick идёт с `confidence=0.9`), то есть порог по уверенности ортогонален
  наблюдаемому классу ошибок. **Что осталось открытым:** wrong-кейсов найдено 3 на ~150
  проверенных живых пиков — класс редкий (~1%), и набор его представляет тонко; пополнять из
  реальных инцидентов (прод-лог несёт `video_id` → `videos.list` → верификация вручную).

- **O. Request-side Gemini API-contract drift — caught by runtime visibility, not a unit test (#340).** When Google changes what the API accepts (e.g. 3.x models reject `thinking_budget=0`, #338), a unit test with a `_FakeClient` **cannot** catch it: the fake encodes our assumption about the request contract and can only confirm it. A live-E2E against real Gemini is a scope-skip (credentials/flake/quota). So the standing safety net is **runtime visibility, not a test**: a `400 INVALID_ARGUMENT` is classified as `ModelConfigRejected` → ERROR log + operator Telegram alert + red job (`config_rejected_models`), instead of a silent `TryNextModel` that green rotation hides. The one unit-testable guard is a **contract test on a real `google.genai.errors.ClientError`** (`test_real_client_error_invalid_argument_routes_to_config_rejected`) — it fails loudly if our `.status` detection drifts from the SDK's actual error shape (which would otherwise ship the whole fix as a green-tested no-op). Recorded so the live-E2E isn't re-opened as work-for-work.
- **P. Prompt-injection resistance of the *real model* — offline structural tests only, no live eval (#308).**
  `test_prompt_injection.py` proves our **defenses** deterministically with a `_FakeClient`: untrusted
  `$title`/`$description` are fenced, fence-sentinel breakout attempts are stripped, hijacked *output*
  is caught by `response_pattern` → marker and HTML-escaped at render. What a `_FakeClient` **cannot**
  show is whether the live Gemini actually obeys the fence under a novel jailbreak — that needs a
  live promptfoo/RAGAS red-team run against real quota (credentials/flake/cost), a negative-ROI
  scope-skip here. Justified by the honest blast radius (`docs/architecture/llm-security.md`): no
  tool-calling / exfiltration → a bypassed fence yields only cosmetic wrong text, itself HTML-escaped.
  A **semantic output-guard for the free-form Steam source** (`steam_charts_mostplayed`, no
  `response_pattern`) is likewise out — adding one changes prod behaviour, tracked as a separate unit.
  Recorded so neither is re-opened as work-for-work.
- **Q. RAGAS summarizer eval — the live LLM-judge runs dev-only, never in CI (#347).**
  `scripts/eval_summarizer.py` scores `summary_ru` faithfulness/answer_relevancy with RAGAS, whose
  metric *is* an LLM-as-judge (+ embeddings). CI unit-tests the pure seams and mocks the single
  `_evaluate_dataset` boundary, so the **baseline number** (mean faithfulness over the golden-set) is
  produced by a **dev run with the judge wired**, not by CI — no API key/quota/cost in the pipeline,
  same class as the trailer `--record`. Accepted, not silent: the harness prints the score and
  `--threshold` gates it; the [harness section](testing.md#eval-harness--summarizer-faithfulness-347) documents
  the seam split. Recorded so «why isn't the RAGAS score in CI?» isn't re-opened as a mock-the-judge
  work-for-work test.
- **R. Dedupe-key edges where the year anchor can't disambiguate (#363).** The kinozal dedupe key is
  the `RU / Original / Year` prefix of the raw title, located by scanning for the first bare-year
  segment (`text_utils.YEAR_SEGMENT_RE`) and dropping the format tail. Two edges are **consciously
  accepted**, characterized but not "fixed": (1) a film whose **RU title IS a bare year** (`2012`,
  `1917` → raw `2012 / 2012 / 2009 / …`) keys on that first-segment year → `"2012"` — a no-op vs. the
  pre-#363 first-segment behaviour, same class as `original_title`'s numeric-original edge (#138 Out of
  scope), pinned by `test_year_titled_film_collapses_to_year`. (2) a **yearless** raw title (no bare-year
  segment at all) falls back to the clean first segment, so yearless namesakes could still collapse — but
  a top-page title without a year is anomalous, and any collapse is already visible in aggregate via the
  `_dedup_and_log_coverage` INFO line (`N extracted (M after dedup-collapse)`), so it isn't a silent §IV
  loss. Both accepted because the disambiguating signal (a distinct year) is genuinely absent; recorded so
  a year-titled namesake collision isn't re-opened as a regression.
- **S. Anti-bot 403: root cause is the *egress IP*, and it can only be observed in prod (#358).**
  Замер 2026-07-25 тем же `curl_cffi==0.15.0`, что и прод: локально (residential IP)
  `impersonate="chrome"` → 200, **без** impersonate → 403, `chrome124`/`chrome131` → те же 200;
  в CI (датацентровый IP GitHub Actions) → 403 семь суток подряд (18–24.07), при этом 14.07 и 17.07
  тот же CI получал 200. В теле блока **нет** `cdn-cgi/challenge-platform` и turnstile. Выводы,
  записанные чтобы их не переоткрывали: (1) TLS-фингерпринт (#217) **исправен** — пин свежего
  `impersonate`-таргета лечит ничего; (2) **Playwright бесполезен** — решать нечего, это плоский
  WAF-отказ, а не JS-челлендж; (3) причина — репутация датацентрового IP (вероятностный bot score),
  лечится только другим egress'ом (прокси / self-hosted runner) — решение оператора, вынесено в
  отдельную issue. **Почему это не тест:** воспроизвести можно лишь с CI-IP, а исход зависит от
  внешнего скоринга — любой «тест» мерил бы погоду у Cloudflare. Стоящая на этом месте страховка —
  **видимость** (§IV): `describe_block` пишет per-attempt WARNING (`cf-ray`, `cf-mitigated`,
  Cloudflare error code, `<title>`, размер тела), по которому следующий инцидент разбирается из
  лога, а не повторным ручным замером. Сам форматтер — pure и покрыт unit'ами
  (`TestBlockDiagnostics`) поверх **реальной** блок-страницы `tests/fixtures/cloudflare_block_403.html`
  (IP/Ray-ID обезличены): reality-anchor держит контракт «сигнал в `<title>`, а не в префиксе тела»
  — первые ~200 символов настоящей страницы это `<!DOCTYPE html> <!--[if lt IE 7]>…`.

- **T. YouTube throttle/retry: механизм отвергнут замером, поэтому тестов на него нет и не будет (#384).**
  План #384 предполагал `tenacity` (`wait_exponential`, 3 попытки, глобальный give-up) и test plan
  под него. Замер 2026-07-26 (Service Usage API): `search.list` — **100 запросов в сутки**, квота
  дефолтная и поднятой быть не может (billing выключен), а прогон на 170 фильмов просит 340. Лимит
  считается **в запросах за сутки**, поэтому паузами не лечится: пейсинг раздаёт те же 100 ровнее,
  retry отбирает квоту у следующего фильма. Вместо него — остановка обогащения по первому квотному
  отказу (`YoutubeQuotaExhausted`, покрыто `TestQuotaStop` + `TestQuotaDetection`); rationale — в
  [`pipeline.md`](pipeline.md#trailer-retrieval-and-selection-140-141-144). Записано сюда, чтобы
  `tenacity`+`sleep` не переоткрыли как «очевидно недостающий retry»: это не пробел покрытия, а
  отсутствующий по замеру код. Единственный путь к полному охвату — смена источника (TMDB), не retry.
  **Что покрыто, а не пропущено:** предикат квотных ошибок нужен и существует — `_is_quota_error`
  пинится reality-anchor'ом на настоящем `googleapiclient.errors.HttpError` (429 legacy `errors[]`,
  403 `quotaExceeded`, ErrorInfo `details[]` в SCREAMING_SNAKE), потому что `.reason` — человеческий
  текст, а машинный код живёт в `error_details`. Промежуточный вариант с фиксированным бюджетом
  (`_TRAILER_RUN_BUDGET = 45`) отвергнут до мержа: угаданное число, ломается на втором прогоне в
  сутки и занижает охват на одноветочных items — в коде не осталось.

- **U. Качество подбора трейлера для игр измеряется ОДНИМ кейсом (#385 → #412).**
  #385 чинил **классификацию** (игровой листинг `t=7` → профиль без `original_title`, чтобы в
  YouTube не уходило `x64 2024 trailer`) и игровых кейсов в golden-set не оставил вовсе — поэтому
  `scripts/eval_trailers.py` по играм не двигался, и регрессию #412 (у локализованной игры гасился
  настоящий оригинал) гейт увидеть не мог. #412 добавил **живой** кейс `Marvel Человек-Паук 2`
  (пул записан 2026-07-29 через прод-`search_candidates`, accept-set — четыре официальных трейлера
  PlayStation/Marvel Entertainment, trap — четыре трейлера одноимённого фильма 2026). Синтетику
  по-прежнему не добавляем — прецедент #359: догадка в эталоне отравляет eval. Один кейс — это
  полюс, а не метрика класса: §III всё ещё запрещает обещать «трейлеры для игр подбираются
  хорошо», он лишь фиксирует, что этот конкретный класс промаха больше не проходит молча.
  **Кейс сразу окупился:** первоначальный дизайн #412 («базовая часть названия — fallback при
  пустом relevant») сел в скоркарту как `WRONG` — полное название с изданием подряд входит в
  заголовок нарезки костюмов, и до настоящих трейлеров отбор не доходил. Без этого кейса правка
  уехала бы в прод зелёной.
  **Что ушло вместе с классификацией (#412):** `item.raw["kinozal_is_game"]` был единственной
  per-item ground-truth меткой «это игра». Дискриминатор переехал в грамматику заголовка, метка
  стала мёртвой и удалена; будущая игровая под-метрика восстановит её из формы raw-заголовка
  (`x64`/`RU` во 2-м сегменте + хвост `PC (Windows)`), а не из категории листинга.
  **Три границы этого фикса, сознательно оставленные открытыми (review #412):**
  1. **Скобка-часть во 2-м сегменте** (`… / Dune (Part Two) / …`) схлопнула бы базу до франшизы,
     и `_title_tokens_in` со своим numeric-skip пропустил бы чужой ролик. Класс замерен и почти
     пуст: 1 заголовок из 238 со скобкой во 2-м сегменте (`Heroes of Might and Magic IV (4)
     (Complete)`), причём номер части там уже в самом названии, так что схлопывания нет. Гард-тест
     не пишем — фиксировать нечего; при появлении класса это станет багом с живым примером.
  2. **Запрос по-прежнему уходит с изданием** (`Marvel's Spider-Man 2 (Digital Deluxe Edition)
     2025 trailer`): срез живёт только в relevance. По замеру пул при этом нормальный (кейс HIT),
     то есть мы зависим от терпимости YouTube к лишним токенам — если она изменится, промах
     проявится как обычный miss-маркер.
  3. **Baseline-гейт не сторожит `html.unescape`**: golden-фикстура мигрирована в декодированную
     форму (как её отдаёт `--record` через прод-`search_candidates`), поэтому уход unescape из
     `_search_one` оставит скоркарту зелёной. Регрессию ловит только
     `tests/test_youtube.py::TestSearchCandidates::test_html_entities_decoded`.
  4. **Год у игр — год репака, а не релиза.** Профиль игры несёт год kinozal-раздачи (2025 у
     PC-порта), а официальные трейлеры сняты в 2023–2024. Кейс #412 проходит год-фильтр лишь
     потому, что в заголовках этих трейлеров года нет вообще; заголовок вида `… (2023) Launch
     Trailer` был бы отброшен `title_year_matches`. Эффект предсуществующий, но **достижимым**
     стал только сейчас — до #412 игры не доходили до retrieval с настоящим названием.
  **Известное смещение, которое это скрывает:** `HeuristicStrategy._rank` (`trailer_strategy.py`)
  первично ранжирует по кириллице в заголовке кандидата — правило, выведенное для фильмов с русским
  дубляжом (#141/#315). У игры название латиницей и русского дубляжа не бывает, поэтому русский
  лец-плей может обойти официальный трейлер. Эффект **предсуществующий** (был и до #385, просто
  запрос тогда уходил с мусорным `x64`), не регрессия — записан здесь, чтобы его не открыли как
  новый баг #385 и не «починили» правку ранжирования без метрики, которой пока нет. На кейсе #412
  смещение не выстрелило: русские кандидаты в пуле — трейлеры одноимённого фильма 2026, их
  отсекает год. То есть один кейс его не опровергает и не подтверждает.

- **V. Секрет-гейт: захваченные HTML-фикстуры вне скана, а ушедшие с `pre-commit` хуки не
  заменяются (#389).** `ci_check` шаг `secrets` покрыт `tests/test_secrets_gate.py` (подсадной ключ →
  non-zero, чистый файл → 0, сбой `git ls-files` и пустой список → видимый `exit 1`). **Вне скана
  сознательно:** `tests/fixtures/**/*.html` — захваченная чужая разметка, где хеши ассетов дают
  high-entropy FP по построению (15 находок на две фикстуры). Исключение файлом, а не baseline'ом:
  baseline при совпадении переписывает себя и возвращает rc=3, а перегенерация — это кнопка «сделать
  гейт зелёным» для настоящего утёкшего ключа (rationale — [`ci.md`](ci.md#secret-scan-secrets-389)).
  Цена: ключ, вписанный **внутрь** такой фикстуры, гейтом не ловится — остаётся серверный слой
  (GitHub push protection). **Второе — не пробел, а отсутствующий код:** вместе с `.pre-commit-config.yaml`
  ушли `check-yaml`/`check-toml`/`check-json`/`trailing-whitespace`/`end-of-file-fixer`; они не
  исполнялись **ни разу** (`core.hooksPath` = `.githooks`), поэтому регрессии нет и замену им этот PR
  не заводит. Записано, чтобы «а где проверка YAML?» не переоткрыли как пробел покрытия: это
  осознанный не-скоуп, отдельная единица (workflow #4).

- **W. Промпты ревьюеров: форма стережётся, семантика — нет (#374, #392).** Оба
  ревьюера — cloud (`.github/workflows/claude-review.yml`) и локальный
  (`.claude/agents/architect-reviewer.md`) — раньше несли severity-фильтр *на стадии
  поиска*, и модель исполняла его буквально: находка молча не доходила до PR. Гарды
  ловят **известные формы**, и каждый — свои, потому что промпты разные:
  `tests/test_claude_review_workflow.py` (англоязычный промпт) — императив
  подавления в начале строки, наличие `severity` **и** `confidence`, отсутствие
  gag-строки `no blocking issues`; `tests/test_agent_frontmatter.py`
  (русскоязычный промпт, поэтому не regex по началу строки, а снятые формулировки
  дословно) — `не раздувай` / `беспощаден` / `краткость по умолчанию` не вернулись,
  наличие `confidence` и `blocking`. Второй применяется **только** к агентам,
  декларирующим findings-контракт (#407) — прочим агентам эти токены не нужны.
  **Сознательно НЕ
  покрыта семантическая перефразировка** («будь избирателен», «only report what
  matters»): проверка смысла промпта — это LLM-вызов на каждый прогон suite, то есть
  дороже и менее детерминированно, чем предмет проверки; а регексп по открытому
  множеству формулировок даёт change-detector, скроенный под текущий текст (первая
  версия гарда #374 именно так и выглядела — с карв-аутом «разрешено, если рядом
  слово ruff» — и была забракована на architect-review). Остаточная защита —
  проза [`ci.md`](ci.md#coverage-first-prompt-no-filtering-at-the-search-stage-374) и
  сам plan-ревьюер. Записано, чтобы «а почему нет теста на промпт» не переоткрыли:
  тест есть, отклонена именно семантическая его половина.

- **X. Кодировка subprocess: гард стережёт сторону родителя, не ребёнка (#364).**
  `tests/test_subprocess_encoding.py` (AST по `scripts/**`, `src/**`, `tests/**`)
  требует явный `encoding` у вызова, который захватывает вывод в текстовом режиме —
  без него Windows декодит кодовой страницей ОС и теряет весь вывод на первом
  кириллическом байте. **Сознательно не покрыта половина ребёнка:** дочерний Python
  пишет в pipe своей ANSI-кодировкой, пока не получит `PYTHONUTF8=1`/`-X utf8`, и
  такой call-site гард пометит зелёным. Статически это не проверить: нужный env
  собирается в рантайме (`ci_check` передаёт `-X utf8` детект-секретам,
  `test_github_trending_pipeline` — `PYTHONUTF8` в собранном `env`), а требовать
  флаг у **каждого** запуска Python дало бы ложные срабатывания там, где вывод
  заведомо ASCII. **Общий `run_text()`-хелпер ОТВЕРГНУТ окончательно (#410),** а не
  отложен, как записывала первая версия этого пункта, и причина техническая:
  репо-корень **никогда не на `sys.path`** при документированном CLI
  `python scripts/foo.py` (`sys.path[0]` = `scripts/`, editable-install добавляет
  только `src/`) — механика уже описана в `scripts/issue_branch.py`. Каждый
  скрипт получил бы importlib-бутстрап (~8 строк), то есть бойлерплейта больше,
  чем удаляемого кода, а `python -m scripts.foo` сломал бы CLI, `settings.json`,
  pre-push и доки. Плюс три call-site в хелпер не влезают в принципе
  (`ci_check._run` и `new_branch._run(capture=False)` намеренно **не** захватывают
  вывод, `ci_check._tracked_files` намеренно **бинарный**). Инвариант вместо
  хелпера держит **правило в самом гарде** — и, в отличие от хелпера, оно ещё и
  мешает написать дефолт заново. И **отклонён `PYTHONUTF8=1` как единственное
  лекарство** — он чинит обе половины сразу, но живёт в состоянии среды, невидим
  на свежем клоне и не защищает call-site, запущенный иначе; гарантия слабее, чем
  у гейта на исходнике. Записано, чтобы «а почему не хелпер / не переменная среды»
  не переоткрыли как work-for-work.

  **Границы правила «дефолт на выводе запрещён» (#410).** Оно распознаёт
  `<выражение>.stdout or …` / `.stderr or …` по **атрибуту слева от `or`** и
  сознательно НЕ ловит: (а) переприсваивание в промежуточную переменную
  (`out = proc.stdout` → `out or ""`), (б) `getattr(proc, "stdout") or ""`,
  (в) эквивалент через `if proc.stdout is None: proc.stdout = ""`. Расширять до
  трассировки значений — это уже поток данных, а не синтаксис: цена растёт
  качественно, а ловится тот же один класс. Правило гарантирует, что **прямая**
  идиома не вернётся; обходной формы сегодня в репо нет ни одной, и её появление
  ловит человек на ревью. Правило намеренно узкое ещё и потому, что широкое
  («любой `or ""`») флагало бы легитимные дефолты (`os.environ.get(...) or ""`),
  и его пришлось бы ослаблять — а глушить pytest-ассерт нечем, `noqa` у него нет.

  **Покрыты не все новые ветки — осознанно (#410).** Тестами закреплены три
  **различающих** решения, где перепутать исходы дорого: `check_red` → код 2
  («гейт сломан»), а не 1 («тесты не красные») — `/implement` шаг 3 трактует их
  по-разному; `hooks._run_ruff` → сигнал `setup_broken`, а не исключение (иначе
  stderr уходит пользователю, но не агенту); `ci_check._tracked_files` →
  «file set is unknown», а не вводящее в заблуждение «no files to scan». Ветки в
  `open_pr`/`set_issue_priority`/`issue_branch`/`validate_issue_sections`/
  `verify_pr_link` остались **без отдельных тестов**: у них один и тот же исход
  («видимая ошибка вместо пустоты»), различающего решения там нет, и пять копий
  одного теста были бы change-detector'ами. Их защищает правило гарда: вернуть
  дефолт нельзя, не покраснив `test_no_output_defaults`. Записано, чтобы пропуск
  был решением, а не забывчивостью.

- **Z. Целостность relative-ссылок между `.md` гейтом не стережётся (#418).** Переезд
  runtime-половины `ci.md` в `operations.md` перецелил 8 входящих указателей, половина
  из которых — проза и комментарии в коде, а не markdown-ссылки. Гейта на «файл
  существует + якорь резолвится» **нет**, и он сознательно не заведён здесь: это
  отдельная логическая единица (запись в `CHECKS` + parity-строка в `ci.yml` + тесты +
  цена на каждом прогоне), а не довесок к docs-PR. Важнее — **найденный инцидент им бы
  и не ловился**: комментарий в `test_kinozal_pipeline.py` ссылался на `ci.md:435`, то
  есть **по номеру строки**; файл существовал, якоря не было вовсе, и ссылка протухла
  молча. Root cause того класса — сами line-number-ссылки, он снят заменой обеих таких
  ссылок на якоря секций. Записано, чтобы будущий link-checker не обосновывали этим
  инцидентом — он про другой класс.

- **AA. «Док не должен снова разрастись» гейтом не стережётся (#419).** Свёртка `ci.md`
  (618 → 417 строк) убрала накопленную археологию решений, у которой уже есть дом —
  тела issue #255/#235/#396. Напрашивающийся анти-рецидив-гейт «файл не длиннее N строк»
  отвергнут как **Goodhart**: под порогом ужимается формулировка, а не археология, то есть
  гейт зелен ровно тогда, когда дефект замаскирован. Семантическое суждение «сколько здесь
  прозы-обоснования, а сколько правила» — тот же класс, что детектор семантических дублей,
  который репо сознательно не строит (`project-map.md`); детектор дал бы ложное покрытие
  (§IV). **Настоящий анти-рецидив здесь — формат, а не правило:** в строку таблицы или
  ledger'а пост-мортем физически не влезает, в свободную секцию — влезает. Формат > проза >
  гейт. Записано, чтобы «а почему нет гейта на объём доков» не переоткрыли как
  work-for-work. **Граница записи:** она про доки, читаемые по требованию, где размер — лишь
  *прокси* качества. Для always-load набора гейт наоборот заведён
  (`tests/test_always_load_budget.py`, #375): там байты — не прокси, а сама плата с каждой
  сессии, и порог работает храповиком, а не нормативом качества. Различает эти случаи вопрос
  «метрика — прокси или сама стоимость», а не «размер гейтить нельзя».

- **AB. Бюджет always-load меряет у́же сессионной преамбулы (#375).** `test_always_load_budget`
  считает `CLAUDE.md` + `.claude/rules/*.md` без `paths:`, но в преамбулу входят ещё
  `description:` сабагентов и слэш-команд и индекс `MEMORY.md`. Один порог на разнородную сумму
  мешал бы диагностику — красный тест не сказал бы, где вырос, — поэтому **cost-shifting туда
  гейт сознательно не ловит** (равно как и перенос текста в `docs/architecture/*`, который агент
  всё равно читает по требованию). Прирост именно в agent/command-frontmatter — повод завести
  **второй** счётчик, а не расширять этот.

**Scope-skip (can't run without live credentials) — see [What does NOT get tested](testing.md#what-does-not-get-tested-in-this-repo):**

- **J. Concurrent state — true *parallel* execution is a non-target** (serial daily cron, no
  overlap → a crash/concurrency simulation would be work-for-work). Realistic failure modes
  *are* covered: rerun-after-crash idempotency (dedupe index re-read) and notify-then-store
  ordering (a failed-notify item isn't stored → retried next run, no silent loss).
  Cell-level partial `gspread` writes are scope-skip (live credentials).

## Modules without dedicated tests

| Module | Reason | Mitigation |
|---|---|---|
| `youtube.py::Youtube` (live-client wrapper: `__init__` + `search_candidates` method) | Requires live YouTube API (`build()` + `API_KEY`) | Pure retrieval `search_candidates(client, profile)`/`_search_one` **is** directly tested (`test_youtube.py::TestSearchCandidates` via an injected fake `client`, the DI boundary, #140); only the thin live-`build()` wrapper is untested. `get_trailer_url`/`_search_youtube` удалены в #144 (прод перешёл на `search_candidates` + `HeuristicStrategy`) |
| `tmdb_trailer.py::TmdbClient` (`resolve`/`_get`/`_find_movie_id`) | Requires live `TMDB_TOKEN` + network — retrieval boundary (DI, mirror of `youtube.py`) | Pure selection `pick_trailer` **is** directly tested (`test_tmdb_trailer.py`, 7 cases); only the network boundary is untested, same §II precedent as `youtube.py`'s live-client wrapper (#329) |
| `text_utils.py` | Small utility | Indirect coverage via `test_kinozal_pipeline.py::TestTitleYearMatches` |
| `*_pipeline.py` `if __name__ == "__main__"` blocks | CLI wiring of live `gspread`/env — needs live credentials | **Scope-skip**, guarded two ways since the package migration ([#237](https://github.com/ekolvah/kinozal_scraper/issues/237)): (1) **mypy is load-bearing** — `pip install -e .` + native package resolution means mypy type-checks the `__main__` block (incl. its `from kinozal_scraper.X import …`), catching a mis-wired/mis-renamed import that the import-only `test_package_importable.py` cannot; (2) the daily cron as §IV «cron = E2E smoke». The large uncovered blocks in `coverage.py` are these runners, not logic gaps |
| Package import-resolution & repo layout | A module failing to resolve as `kinozal_scraper.X`, or source drifting back to a flat `src/*.py` layout | `test_package_importable.py::TestPackage` (all modules import as `kinozal_scraper.X`); `test_repo_layout.py::TestLayout`. (The #237 B1 empty-/nested-scan guard moved off the retired `test_check_headers.py` — [#253](https://github.com/ekolvah/kinozal_scraper/issues/253) replaced `check_headers.py` with ruff `D100`/`D104`/`D419`; the "mis-pointed/empty `src/` scanned nothing" failure mode is now subsumed by these two guards, which fire strictly harder — 17 hard-coded imports + layout-drift — than the old zero-file check) |
| Telethon session rotation (mint a `StringSession`, set the secret, revoke the old session in the Telegram app) | Interactive login against live Telegram, performed by an operator roughly once per incident | **Scope-skip** (#386, replaces the `crypto.py` glue entry that left with the module): there is no automatable surface — the code side *is* covered (`require_env` rejects an empty secret, `TestTelethonReaderAuth` pins StringSession-only auth and a fail-fast on a revoked session). The recipe lives in [operations.md](operations.md#minting-a-new-telethon_session); deliberately a doc snippet, not a script — a once-a-year human interactive is not the deterministic pipeline step "скрипты > инструкции" targets |
| `scripts/probe.py` — живой запрос к soldoutticketbox.com | Замер вероятности блокировки требует настоящего датацентрового IP и настоящего Cloudflare | **Scope-skip** (#396): живой запрос из теста и есть работа самого пробника в CI — дублировать его в pytest значило бы гонять сеть на каждом прогоне ради того, что и так меряется каждые 3 часа. Покрыты именно границы, где замер может тихо испортиться: одна попытка на замер (не 4), те же request-kwargs что у прода (`TestSharedRequestKwargs`), строка на **каждый** исход включая 200, разделение «HTTP-исход → 0» / «сбой инструмента → ≠0», expiry и оба пути (HTML + постер) |
