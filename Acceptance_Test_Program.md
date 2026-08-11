# Программа и Методика Испытаний (ПМИ)
## Приемо-сдаточные испытания минимально жизнеспособного прототипа (MVP)

**Версия документа:** 1.1  
**Дата:** 7 августа 2026 г.  
**Язык:** Русский  

---

## 1. Назначение документа

Настоящий документ содержит программу и методику приемо-сдаточных испытаний минимально жизнеспособного прототипа (MVP). Документ предназначен для последовательной проверки полного пользовательского сценария MVP, охватывающего взаимодействие Telegram Bot, Orchestrator, GitHub Adapter, Coding Agent Adapter и GitHub Actions Adapter.

---

## 2. Сценарии приемочных испытаний

### TEST-001 — Receive task from Telegram

- **Purpose**: Проверить возможность приёма описания задачи от авторизованного пользователя в Telegram Bot.
- **Preconditions**: Telegram Bot запущен; пользователь входит в список авторизованных пользователей.
- **Steps**:
  1. Пользователь отправляет команду инициирования новой задачи в Telegram Bot.
  2. Пользователь вводит текстовое описание задачи.
  3. Пользователь подтверждает отправку задачи через интерактивную кнопку.
- **Expected Result**: Бот принимает описание задачи, регистрирует её в системе и переводит задачу в начальное состояние пайплайна.
- **PASS Criteria**: Задача зарегистрирована в Orchestrator; пользователь получает сообщение-подтверждение в Telegram.
- **FAIL Criteria**: Бот не реагирует на команду; сбрасывает ввод; не регистрирует задачу.

---

### TEST-002 — Create GitHub Issue

- **Purpose**: Проверить автоматическое создание GitHub Issue на основе принятой из Telegram задачи.
- **Preconditions**: TEST-001 выполнен успешно; GitHub Adapter настроен с валидным токеном и репозиторием.
- **Steps**:
  1. Orchestrator передаёт задачу в GitHub Adapter.
  2. GitHub Adapter создаёт Issue в целевом репозитории через GitHub REST API.
- **Expected Result**: В целевом репозитории GitHub появляется новая Issue с описанием задачи из Telegram.
- **PASS Criteria**: Issue создана на GitHub; получены номер Issue и её URL.
- **FAIL Criteria**: Issue не создана в репозитории или API возвратил ошибку.

---

### TEST-003 — Assign GitHub Copilot Coding Agent

- **Purpose**: Проверить назначение GitHub Copilot Coding Agent на созданную Issue.
- **Preconditions**: TEST-002 выполнен успешно; Issue существует в репозитории.
- **Steps**:
  1. Coding Agent Adapter запрашивает назначение `github-copilot[bot]` на созданную Issue.
  2. Orchestrator переводит состояние задачи в статус активного кодинга.
- **Expected Result**: В поле Assignees у Issue на GitHub появляется учётная запись `github-copilot[bot]`.
- **PASS Criteria**: `github-copilot[bot]` добавлен в список назначенных исполнителей Issue; состояние задачи обновлено.
- **FAIL Criteria**: Исполнитель не назначен или состояние задачи не обновилось.

---

### TEST-004 — Create Pull Request

- **Purpose**: Проверить фиксацию создания Pull Request агентом Copilot.
- **Preconditions**: TEST-003 выполнен успешно; Copilot начал генерацию кода.
- **Steps**:
  1. GitHub Copilot создаёт ветку и открывает Pull Request для связанной Issue.
  2. GitHub Adapter фиксирует событие создания PR через входящий вебхук или опрос API.
- **Expected Result**: Orchestrator связывает номер и URL созданного PR с текущей задачей и переводит задачу в соответствующее состояние.
- **PASS Criteria**: PR привязан к задаче в Orchestrator; состояние задачи обновлено до `pr_open`.
- **FAIL Criteria**: Создание PR не зафиксировано или ссылка на PR не сохранена.

---

### TEST-005 — Run GitHub Actions

- **Purpose**: Проверить отслеживание запуска и выполнения CI-проверок в GitHub Actions для открытого PR.
- **Preconditions**: TEST-004 выполнен успешно; в репозитории настроен GitHub Actions workflow.
- **Steps**:
  1. При открытии PR запускается GitHub Actions workflow.
  2. GitHub Actions Adapter принимает вебхук `workflow_run` со статусом `in_progress` или `queued`.
- **Expected Result**: Orchestrator переводит состояние задачи в `ci_running`.
- **PASS Criteria**: Состояние задачи успешно переведено в `ci_running`.
- **FAIL Criteria**: Запуск GitHub Actions проигнорирован; состояние задачи не обновилось.

---

### TEST-006 — Failed tests → @copilot fix the failing tests

- **Purpose**: Проверить обработку падения CI-тестов и отправку инструкции авто-исправления Copilot.
- **Preconditions**: TEST-005 выполнен успешно; GitHub Actions workflow завершился со статусом `failure`.
- **Steps**:
  1. GitHub Actions Adapter принимает вебхук `workflow_run` со статусом `failure`.
  2. GitHub Actions Adapter извлекает фрагмент лога с описанием ошибки.
  3. Coding Agent Adapter публикует комментарий к Issue или PR с текстом `@copilot fix the failing tests` и фрагментом лога.
- **Expected Result**: В комментарии на GitHub публикуется команда авто-исправления с логом падения тестов.
- **PASS Criteria**: Комментарий с директивой `@copilot fix the failing tests` подтверждён в GitHub.
- **FAIL Criteria**: Комментарий не опубликован или отправлен без директивы исправления.

---

### TEST-007 — Successful tests

- **Purpose**: Проверить реакцию системы на успешное прохождение CI-тестов в GitHub Actions.
- **Preconditions**: TEST-005 выполнен успешно или повторная итерация после исправления; workflow завершился со статусом `success`.
- **Steps**:
  1. GitHub Actions Adapter принимает вебхук `workflow_run` со статусом `success`.
  2. Orchestrator переводит задачу в состояние `review` или готовности к завершению.
- **Expected Result**: Задача успешно проходит этап CI и готовится к финальному уведомлению.
- **PASS Criteria**: Orchestrator регистрирует успешное прохождение CI-тестов; состояние задачи обновлено.
- **FAIL Criteria**: Успешный статус CI не зафиксирован; задача осталась в промежуточном состоянии.

---

### TEST-008 — Notify user in Telegram

- **Purpose**: Проверить отправку итогового уведомления пользователю в Telegram со ссылкой на Pull Request.
- **Preconditions**: TEST-007 выполнен успешно; задача перешла в финальное состояние.
- **Steps**:
  1. Orchestrator инициирует отправку итогового сообщения через Telegram Bot.
  2. Telegram Bot отправляет сообщение пользователю в чат.
- **Expected Result**: Пользователь получает в Telegram финальное сообщение со ссылкой на готовый Pull Request и статусом выполнения.
- **PASS Criteria**: Сообщение с прямыми ссылками на PR и Issue доставлено пользователю в Telegram.
- **FAIL Criteria**: Уведомление не отправлено или не содержит ссылку на PR.

---

### TEST-009 — Copilot requests clarification from the user

- **Purpose**: Проверить, что система обнаруживает уточняющий вопрос от Copilot в комментарии к Issue и доставляет его пользователю в Telegram.
- **Preconditions**: TEST-003 выполнен успешно; `github-copilot[bot]` назначен на Issue и оставил комментарий с уточняющим вопросом.
- **Steps**:
  1. GitHub Adapter получает вебхук о новом комментарии к Issue от `github-copilot[bot]`.
  2. Coding Agent Adapter анализирует содержимое комментария и определяет его как уточняющий вопрос.
  3. Orchestrator передаёт вопрос в Telegram Bot.
  4. Telegram Bot отправляет текст вопроса пользователю в чат.
- **Expected Result**: Пользователь получает в Telegram сообщение с текстом уточняющего вопроса от Copilot.
- **PASS Criteria**: Вопрос от Copilot корректно доставлен пользователю в Telegram без изменения содержимого.
- **FAIL Criteria**: Вопрос не обнаружен; не доставлен; содержимое искажено или доставлено некорректному пользователю.

---

### TEST-010 — User sends an answer back to Copilot through Telegram

- **Purpose**: Проверить, что ответ пользователя из Telegram корректно публикуется как комментарий к Issue на GitHub.
- **Preconditions**: TEST-009 выполнен успешно; пользователь видит вопрос от Copilot в Telegram и готов ответить.
- **Steps**:
  1. Пользователь вводит ответ и отправляет его в Telegram Bot.
  2. Telegram Bot передаёт ответ в Orchestrator.
  3. Orchestrator передаёт ответ в Coding Agent Adapter.
  4. Coding Agent Adapter публикует ответ пользователя как комментарий к Issue на GitHub.
- **Expected Result**: Ответ пользователя появляется в комментариях к Issue на GitHub.
- **PASS Criteria**: Комментарий с текстом ответа пользователя опубликован в Issue на GitHub; Orchestrator переходит в состояние ожидания дальнейших действий Copilot.
- **FAIL Criteria**: Ответ не опубликован в GitHub; содержимое искажено; ответ направлен в неверный Issue.

---

### TEST-011 — GitHub Actions finishes with FAILURE

- **Purpose**: Проверить корректную обработку события завершения GitHub Actions со статусом `failure` после того, как Copilot внёс правки по результатам ответа пользователя.
- **Preconditions**: TEST-010 выполнен успешно; Copilot внёс изменения в код и открыт Pull Request; GitHub Actions workflow завершился со статусом `failure`.
- **Steps**:
  1. GitHub Actions Adapter получает вебхук `workflow_run` с итоговым статусом `failure`.
  2. GitHub Actions Adapter извлекает фрагмент лога с ошибкой выполнения тестов.
  3. Orchestrator регистрирует факт сбоя CI и переводит задачу в соответствующее состояние.
- **Expected Result**: Orchestrator зафиксировал сбой CI; система готова к публикации команды авто-исправления.
- **PASS Criteria**: Состояние задачи отражает сбой CI; фрагмент лога ошибки извлечён и доступен для последующего использования.
- **FAIL Criteria**: Сбой CI не зафиксирован; лог не извлечён; состояние задачи не обновилось.

---

### TEST-012 — System publishes: @copilot fix the failing tests

- **Purpose**: Проверить, что система публикует команду `@copilot fix the failing tests` с фрагментом лога ошибки непосредственно после регистрации сбоя CI.
- **Preconditions**: TEST-011 выполнен успешно; Orchestrator зафиксировал сбой CI и имеет фрагмент лога ошибки.
- **Steps**:
  1. Orchestrator инициирует новую итерацию исправления через Coding Agent Adapter.
  2. Coding Agent Adapter формирует комментарий с текстом `@copilot fix the failing tests` и прикреплённым фрагментом лога.
  3. Coding Agent Adapter публикует комментарий к Issue или PR на GitHub.
- **Expected Result**: В комментариях к Issue или PR на GitHub появляется директива исправления с логом ошибки.
- **PASS Criteria**: Комментарий с текстом `@copilot fix the failing tests` и фрагментом лога опубликован на GitHub; счётчик итераций увеличен.
- **FAIL Criteria**: Комментарий не опубликован; опубликован без лога; счётчик итераций не увеличен; превышен лимит итераций без уведомления пользователя.

---

### TEST-013 — GitHub Actions succeeds after the fix

- **Purpose**: Проверить, что система корректно обрабатывает успешное завершение GitHub Actions после автоматического исправления Copilot.
- **Preconditions**: TEST-012 выполнен успешно; Copilot внёс правки согласно директиве; GitHub Actions workflow завершился со статусом `success`.
- **Steps**:
  1. GitHub Actions Adapter получает вебхук `workflow_run` с итоговым статусом `success`.
  2. Orchestrator регистрирует успешное прохождение CI и переводит задачу в состояние `review` или готовности к завершению.
- **Expected Result**: Orchestrator зафиксировал успешное прохождение CI после итерации исправления; задача готова к финальному уведомлению.
- **PASS Criteria**: Состояние задачи обновлено до `review` или эквивалентного; система готова к финальному уведомлению пользователя.
- **FAIL Criteria**: Успешный статус CI не зафиксирован; задача осталась в промежуточном состоянии.

---

### TEST-014 — Pipeline completes and Telegram sends the final Pull Request link and status

- **Purpose**: Проверить, что по завершении полного цикла пайплайна пользователь получает в Telegram итоговое сообщение со ссылкой на Pull Request и финальным статусом.
- **Preconditions**: TEST-013 выполнен успешно; задача переведена в финальное состояние.
- **Steps**:
  1. Orchestrator фиксирует финальное состояние задачи (`done`).
  2. Orchestrator инициирует отправку итогового уведомления через Telegram Bot.
  3. Telegram Bot формирует и отправляет пользователю сообщение с ссылкой на Pull Request и статусом выполнения.
- **Expected Result**: Пользователь получает в Telegram итоговое сообщение, содержащее: ссылку на Pull Request, ссылку на Issue, финальный статус выполнения задачи.
- **PASS Criteria**: Сообщение содержит все три перечисленных элемента и доставлено именно тому пользователю, который инициировал задачу.
- **FAIL Criteria**: Сообщение не отправлено; не содержит ссылку на PR; доставлено не тому пользователю; содержит неверный статус.

---

## 3. Критерии приёмки MVP

MVP считается принятым, если тесты **TEST-001 — TEST-014** пройдены успешно в полном объёме.

Любой провал одного или более тестов является основанием для отказа в приёмке MVP до устранения выявленных несоответствий.
