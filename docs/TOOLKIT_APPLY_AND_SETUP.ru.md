# Официальный RTX Remix Toolkit: создание проекта и Apply

Продолжение документов [замена меша](TOOLKIT_REPLACEMENT_PIPELINE.ru.md) и [рабочие процессы](TOOLKIT_WORKFLOWS.ru.md). Здесь — создание проекта через wizard и применение результатов ComfyUI через Apply.

## Источники

| Раздел | Файл |
|---|---|
| Создание проекта | `O:\RTX Remix Toolkit\exts\lightspeed.trex.project_wizard.core\lightspeed\trex\project_wizard\core\wizard.py` |
| Apply текстур и мешей | `F:\repos\toolkit-remix-1.6.1-0\source\extensions\lightspeed.trex.comfyui.core\lightspeed\trex\comfyui\core\apply_handler.py` |
| Замена reference | тот же файл + `lightspeed.trex.asset_replacements.core.shared/.../setup.py` из `O:\RTX Remix Toolkit\exts\` |

---

## 1. Создание проекта (wizard)

1. `wizard.py:97-119` — `setup_project()` запускает асинхронный `setup_project_async`; параметр `dry_run` не меняет файлы.
2. `wizard.py:132-144` — создаётся USD-контекст, затем LayerManager, CaptureCore, ReplacementCore и модель схемы проекта.
3. `wizard.py:146-149` — каталоги: `<project>/deps/mods`, `<project>/deps/captures`.
4. `wizard.py:151-178` — для нового проекта создаётся папка mods; если указан `remix_directory`, создаются symlinks на `deps/mods` и `deps/captures`, затем их существование проверяется с таймаутом 5 c.
5. `wizard.py:180-197` — для существующего проекта дополнительно извлекаются RTX IO пакеты; на этом шаге работа заканчивается.
6. `wizard.py:199-214` — новый проект: создаётся project-слой, вставляется capture-слой и уже существующие моды.
7. `wizard.py:216-233` — настраивается новый или существующий mod-файл, сохраняются authoring-слой и project-слой.
8. `wizard.py:242-247` — любое исключение превращается в `(False, сообщение)`; прогресс и логи идут через подписки (`:66-88`).

---

## 2. Apply: общая модель

1. `apply_handler.py:938-964` — текстурный Apply: сопоставить выходы ComfyUI с shader input'ами, открыть stage и edit layer, проверить receipt, записать sidecar'ы и только затем authorить `inputs:*`.
2. `apply_handler.py:825-860` — `_apply_atomically()`: состояние цели классифицируется **до** и **после** await sidecar'ов; если оно изменилось — внешняя правка, Apply отклоняется; мутация USD идёт в одном undo group; при ошибке sidecar'ы откатываются.
3. `apply_handler.py:863-900` — `_revert_atomically()`: та же схема в обратную сторону — сначала восстановление sidecar'ов, проверка состояния, затем USD.
4. `apply_handler.py:250-272` — receipt содержит исходные authored-значения, канонические значения для сравнения и исходные sidecar'ы.
5. `apply_handler.py:275-301` — перед Apply receipt сверяется с текущими путями и значениями; при несовпадении пользователя просят отправить задание заново.
6. `apply_handler.py:316-327, 816-822` — проверка, что stage и edit layer не подменили; блокировка Apply при конфликте.

---

## 3. Apply текстур

1. `apply_handler.py:212-247` — `_get_replacements()`: ключ ComfyUI output ↔ путь shader input; старые ключи по типу текстуры (`albedo`, `normal_ogl`, `normal_dx`) тоже сопоставляются с единственным обработанным ассетом этого типа; если сопоставление неоднозначно — ошибка.
2. `apply_handler.py:700-721` — `_replace_textures()` пишет Value через TextureReplacementCore; `_restore_textures()` восстанавливает исходные authored-значения из receipt.
3. `apply_handler.py:328-353` — `_texture_state()` определяет, находится ли цель в исходном, применённом или изменённом внешне состоянии.
4. `apply_handler.py:966-992` — Revert текстур идёт через `_revert_atomically()` и тот же receipt.

---

## 4. Apply меша

1. `apply_handler.py:995-1007` — `ComfyUIAssetApplyHandler` работает с `MeshOptimizationResult` и owner-примами.
2. `apply_handler.py:1032-1052` — `_classify()` сверяет состояние reference и состояние текстур; если они различаются — это внешняя правка, операция прерывается.
3. `apply_handler.py:1054-1141` —Apply:
   - `mesh_apply_behavior = NONE` → reference не трогаются, пишутся только текстуры (`:1080-1081`);
   - `REPLACE` → удаляются выбранные source reference, включая восстановленный ранее дочерний prim (`:1090-1095`), затем создаётся один сгенерированный ребёнок на owner (`:1100-1126`);
   - owner, который уже является Remix reference, заменяется **in place**: путь и другие reference сохраняются, меняется только source (`:1104-1115`);
   - `APPEND` → `setup.add_new_reference()` с `<Default Prim>` (`:1127-1134`);
   - дочерний prim помечается `_GENERATED_FOR_ATTR` или in-place marker (`:1135-1139`);
   - текстуры пишутся в том же проходе (`:1140`).
4. `apply_handler.py:408-419` — `_replaces_in_place()` решает, можно ли заменить source без создания нового sibling.
5. `apply_handler.py:745-815` — in-place замена и откат: удалить source, поставить новый, при Revert восстановить исходный reference и маркеры.
6. `apply_handler.py:607-680` — `_reference_state()` классифицирует комбинацию source/generated/restored reference и маркеров; по ней определяется «original / applied / external edit».

---

## 5. Важное отличие от нашего плагина

- Toolkit не применяет правки молча: любое изменение цели между submit и Apply (слой, edit target, reference, текстура) считается внешней правкой и прерывает операцию.
- Sidecar'ы и USD меняются в одном восстановимом контуре: ошибка Rollback возвращает оба.
- Для замены через ComfyUI есть три режима (`REPLACE`, `APPEND`, `NONE`) и отдельный in-place путь для уже сгенерированных Remix reference.

---

## Что не описано

- Очередь заданий ComfyUI, API, prompt/persistence codecs — только handlers Apply.
- Реализация `_write_output_metadata`, `capture_metadata_receipt`, `revert_metadata` в других модулях.
- Визуальная часть UI, job queue и диалоги Package.
