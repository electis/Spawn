# OpenAI-совместимый API Spawn

HTTP-сервис для голосового помощника и других клиентов. Он принимает запросы в формате OpenAI Chat Completions и отвечает через `kiro-cli` по ACP. Модель и персонаж выбираются параметрами запроса. Беседы API отдельны от бесед Telegram-бота.

## Быстрый старт

```
cd src
../.venv/bin/python -m api
```

По умолчанию сервер слушает `0.0.0.0:8731`. Проверка: `curl http://127.0.0.1:8731/health`.

## Настройка

Секреты и настройки читаются из `src/.env` (права 0600) и из переменных окружения.

| Имя | Где | По умолчанию | Назначение |
|---|---|---|---|
| `KIRO_API_KEY` | `.env` | обязательна | Авторизация `kiro-cli` |
| `API_KEY` | `.env` | обязательна | Bearer-токен для клиентов API |
| `KIRO_BIN` | окружение | `/root/.local/bin/kiro-cli` | Путь к `kiro-cli` |
| `ACP_HOME` | окружение | `/root/spawn-acp-home` | HOME для процессов `kiro-cli` |
| `API_WORKDIR` | окружение | `/var/lib/kiro-bot/api-workdir` | Рабочая папка сессий API |
| `API_HOST` | окружение | `0.0.0.0` | Адрес прослушивания |
| `API_PORT` | окружение | `8731` | Порт |
| `API_DEFAULT_MODEL` | окружение | `claude-haiku-5.5` | Модель, если `model` не передан |
| `API_MAX_PROCESSES` | окружение | `4` | Сколько процессов `kiro-cli` работают параллельно |

Сервер не запустится, если `API_KEY` или `KIRO_API_KEY` не заданы.

## Авторизация

Все пути, кроме `/health`, требуют заголовок:

```
Authorization: Bearer <API_KEY>
```

Без заголовка или с неверным ключом ответ: `401`.

## Эндпоинты

### `POST /v1/chat/completions`

Тело запроса (JSON):

| Поле | Тип | Обязательно | Описание |
|---|---|---|---|
| `messages` | список | да | Сообщения с полями `role` (`system`, `user`, `assistant`) и `content` |
| `model` | строка | нет | Модель из `GET /v1/models`. По умолчанию `API_DEFAULT_MODEL` |
| `persona` | строка | нет | Персонаж: `spawn` (по умолчанию) или `writer` |
| `conversation_id` | строка | нет | Продолжить беседу, полученную в предыдущем ответе |
| `stream` | логический | нет | `true` для потокового ответа (SSE). По умолчанию `false` |

`content` может быть строкой или списком частей `{"type": "text", "text": "..."}`. Другие типы частей (изображения, аудио) не поддерживаются.

**Как строится запрос к модели:**
- Без `conversation_id` создаётся новая беседа. Предыдущие сообщения из `messages` передаются в тексте запроса. Персонаж задаётся в начале новой беседы.
- С `conversation_id` в `kiro-cli` отправляется только последнее сообщение `user`. Беседа хранит свою историю. Поле `persona` в продолжении беседы не меняет персонажа: он задан при создании.
- Системные сообщения (`role: system`) добавляются в начало текста запроса.
- Последнее сообщение должно иметь роль `user`.

### `GET /v1/models`

Список моделей, которые принимает `kiro-cli`:

```json
{"object": "list", "data": [{"id": "claude-haiku-5.5", "object": "model", "created": 0, "owned_by": "kiro"}]}
```

Список берётся при запуске сервера. Чтобы увидеть новые модели, перезапустите сервер.

### `GET /health`

Без авторизации. Ответ: `{"status": "ok"}`.

## Ответ без потока

```json
{
  "id": "chatcmpl-3f1c...",
  "object": "chat.completion",
  "created": 1795621071,
  "model": "claude-haiku-5.5",
  "conversation_id": "sess_0b337351-4678-4fa2-a130-95b9b5365b1c",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "Привет."},
      "finish_reason": "stop"
    }
  ],
  "usage": {"credits": 0.0118, "context_usage": 0.887}
}
```

- `conversation_id` нужно сохранить и передавать в следующих запросах этой беседы.
- `finish_reason` всегда `stop`. Если модель отказалась отвечать, текст ответа содержит сообщение об отказе.

## Ответ с потоком (`stream: true`)

Ответ приходит как `text/event-stream`. Каждое событие начинается с `data: `. Фрагменты текста приходят в `choices[0].delta.content`. Последний фрагмент содержит `finish_reason: "stop"`, поле `usage` и `conversation_id`. Затем приходит строка `data: [DONE]`.

```
data: {"id": "chatcmpl-...", "object": "chat.completion.chunk", "created": 1795621072, "model": "claude-haiku-5.5", "conversation_id": null, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Готово."}, "finish_reason": null}]}

data: {"id": "chatcmpl-...", "object": "chat.completion.chunk", "created": 1795621072, "model": "claude-haiku-5.5", "conversation_id": "sess_bb9e...", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"credits": 0.0051, "context_usage": 0.896}}

data: [DONE]
```

Поле `conversation_id` в первых фрагментах может быть `null`. Окончательное значение приходит в последнем фрагменте. Если ошибка возникла уже во время потока, сервер отправляет событие `data: {"error": {...}}` и затем `[DONE]`.

## Поле `usage`

`kiro-cli` не возвращает количество токенов, поэтому поля `prompt_tokens`, `completion_tokens` и `total_tokens` отсутствуют.

| Поле | Значение |
|---|---|
| `credits` | Кредиты, списанные за этот запрос. Число с плавающей точкой или `null` |
| `context_usage` | Доля заполнения контекста беседы после запроса, от 0 до 1. Или `null` |

`null` означает, что `kiro-cli` не прислал это значение в ответ.

## Ошибки

Формат ошибки:

```json
{"error": {"message": "unknown model: no-such-model", "type": "invalid_request_error", "code": null}}
```

| Код | Когда |
|---|---|
| `400` | Неверное тело запроса, неизвестный `persona`, неизвестная `model`, некорректный `conversation_id`, последнее сообщение не от `user` |
| `401` | Нет или неверный `Authorization` |
| `404` | `conversation_id` не найден среди сохранённых бесед |
| `502` | `kiro-cli` не отвечает или вернул ошибку (`type: api_error`) |

Тексты ошибок не содержат ключей и содержимого окружения.

## Персонажи

| `persona` | Персонаж |
|---|---|
| `spawn` | Спаун, по умолчанию |
| `writer` | Писатель (Марк Твен, отзывается также на «Писец» и «Марк») |

Список задан в `bot/persona.py`.

## Примеры

Обычный запрос:

```
curl http://HOST:8731/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-haiku-5.5", "persona": "spawn", "messages": [{"role": "user", "content": "Привет"}]}'
```

Продолжение беседы:

```
curl http://HOST:8731/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"conversation_id": "sess_0b33...", "messages": [{"role": "user", "content": "Что я спрашивал раньше?"}]}'
```

Потоковый ответ:

```
curl -N http://HOST:8731/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"stream": true, "messages": [{"role": "user", "content": "Расскажи кратко о себе"}]}'
```

## Ограничения

- Сервер говорит по HTTP. Для доступа извне поставьте HTTPS-прокси (например, nginx или Caddy).
- Беседы хранятся в `kiro-cli` в `API_WORKDIR`. Удаления через API пока нет. Старые беседы можно удалить вручную или через `session/delete` в ACP.
- Модель меняется на каждый запрос. Одна беседа может получать ответы разными моделями, если клиент передаёт разные `model`.
- `GET /v1/models` не обновляется без перезапуска.
