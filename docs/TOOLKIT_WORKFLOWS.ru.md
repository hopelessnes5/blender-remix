# Официальный RTX Remix Toolkit: остальные рабочие процессы

Продолжение документа [замена меша](TOOLKIT_REPLACEMENT_PIPELINE.ru.md). Здесь описаны слои, capture, материалы, текстуры и упаковка мода. Источник — исходники установленного Toolkit в `O:\RTX Remix Toolkit\exts\`. Пути ниже сокращены до `exts/`.

Создание проекта и Apply ComfyUI: [TOOLKIT_APPLY_AND_SETUP.ru.md](TOOLKIT_APPLY_AND_SETUP.ru.md).

## Источники

| Раздел | Файл |
|---|---|
| Типы слоёв, создание подслоёв | `lightspeed.layer_manager.core/lightspeed/layer_manager/core/core.py`, `.../data_models/enums.py` |
| Capture-слой и хэши | `lightspeed.trex.capture.core.shared/lightspeed/trex/capture/core/shared/setup.py` |
| Replacement-слой | `lightspeed.trex.replacement.core.shared/lightspeed/trex/replacement/core/shared/setup.py`, `.../layers.py` |
| Материалы | `lightspeed.trex.material.core.shared/lightspeed/trex/material/core/shared/setup.py`, `lightspeed.trex.app.resources/data/validation_schema/material_ingestion.json` |
| Текстуры | `lightspeed.trex.texture_replacements.core.shared/lightspeed/trex/texture_replacements/core/shared/setup.py` |
| Упаковка мода | `lightspeed.trex.packaging.core/lightspeed/trex/packaging/core/packaging.py`, `.../repair/core.py` |

---

## 1. Модель слоёв

1. `lightspeed.layer_manager.core/.../data_models/enums.py:21-30` — типы слоёв: `autoupscale`, `capture_baker`, `capture`, `replacement`, `workfile`. Тип хранится в `customLayerData["lightspeed_layer_type"]`.
2. `lightspeed.layer_manager.core/.../core.py:346-409` — `create_layer()`: создаёт или вставляет подслой, может заменить существующий слой того же типа, перенести содержимое root-слоя, установить edit target и позицию в стеке; перед созданием убирает «битые» слои с таким же именем.
3. `lightspeed.trex.replacement.core.shared/.../setup.py:74-140` — импорт replacement-слоя: без загруженного capture-слоя операция невозможна; старый replacement удаляется; `timeCodesPerSecond` копируется из capture (иначе удаление reference не работает, OM-42663); в `customLayerData` пишется имя игры.
4. `lightspeed.trex.replacement.core.shared/.../layers.py:84-97` — edit target разрешён только на mod-слой и его подслои; capture и root исключены.

---

## 2. Capture-слой и хэши

1. `lightspeed.trex.capture.core.shared/.../setup.py:131-152` — `import_capture_layer()`: открывает capture stage, копирует метаданные в текущий stage, удаляет предыдущий capture-слой, вставляет файл как последний подслой и блокирует его; тип слоя не перезаписывается — файл уже несёт свой `lightspeed_layer_type`.
2. `.../setup.py:161-169` — `is_capture_file()` и `is_layer_a_capture_file()` отличают capture от поассетных USD.
3. `.../setup.py:174-207` — каталог capture, список файлов, иконка игры.
4. `.../setup.py:223-255` — `get_hashes_from_capture_layer()`: для каждого `mesh_<16HEX>` читает `material:binding` и строит пары «материал → все меши с этим материалом». Поэтому правка материала означает правку всех использующих его мешей.
5. `.../setup.py:258-295` — `async_get_replaced_hashes()`: если в change-set есть хэш материала, заменённым считаются все меши из его группы; хэши, отсутствующие в capture, отбрасываются.

---

## 3. Инжекция материалов

1. `lightspeed.trex.app.resources/data/validation_schema/material_ingestion.json:18-33` — контекстный плагин `TextureImporter` с output directory и endpoint по умолчанию.
2. `material_ingestion.json:34-41` — `MaterialShaders`: для материалов проставляется `AperturePBR_Opacity` (subidentifier matching `.*`).
3. `material_ingestion.json:42-69` — `ConvertToOctahedral` и `ConvertToDDS`: нормали и текстуры; оба работают с `AllShaders`, имеют cleanup-каналы и `stop_if_fix_failed`.
4. `material_ingestion.json:70-83` — `MassTexturePreview` и запись метаданных файлов.
5. `lightspeed.trex.material.core.shared/.../setup.py:37-41` — `get_material_layer_stack()` возвращает все слои, которые authorят этот prim.
6. `.../setup.py:43-70` — `get_materials_from_prim()`: для Mesh/Subset берёт `ComputeBoundMaterial()`, обходит инстансированные дети с фильтром по layer.

---

## 4. Замена текстур

1. `lightspeed.trex.texture_replacements.core.shared/.../setup.py:113-178` — `get_texture_prims_assets()`: обход shader-прототипов с фильтром по хэшам, только asset-инпуты shader'а, только поддерживаемые расширения; результат — пары `(путь к inputs:*, абсолютный путь текстуры)`.
2. `.../setup.py:180-253` — `replace_textures()`: валидация prime и ассета (флаг `force`), затем `ChangeProperty` со значением `Sdf.AssetPath`, относительным к edit target; при `None` — удаление input и пустых override-примов вплоть до `/RootNode`.
3. `.../setup.py:255-282` — `get_texture_material()`: поиск материала по connection его MDL-выхода к shader'у.
4. `.../setup.py:284-310` — `get_expected_texture_material_inputs()`: определяет имя input по типу текстуры или по имени файла.

---

## 5. Замена меша

Полный пошаговый разбор — в [TOOLKIT_REPLACEMENT_PIPELINE.ru.md](TOOLKIT_REPLACEMENT_PIPELINE.ru.md). Кратко: отдельный USD-ассет под `/ReferenceTarget/XForms`, `over mesh_<HASH> (references = None)` для замены, дочерний `ref_<uuid>` с `IsRemixRef = 1` и reference на ассет; для добавления reference capture не маскируется; материал наследуется от capture-прима через USD-композицию.

---

## 6. Упаковка мода

1. `lightspeed.trex.packaging.core/.../packaging.py:162-218` — `package()` создаёт задачу; схема содержит `mod_layer_paths`, `selected_layer_paths`, `output_directory`, имя/версию мода и режим `FLATTEN`/`REDIRECT`.
2. `.../packaging.py:224-233` — корневой mod-слой обязан быть в `selected_layer_paths`, иначе упаковка не выполняется.
3. `.../packaging.py:235-266` — открытие stage, временная копия root mod layer, удаление невыбранных подслоёв.
4. `.../packaging.py:274-312` — сбор зависимостей и невалидных ассетов; в `REDIRECT` внешние моды перенаправляются в установленные каталоги; в `FLATTEN` временный root сплющивается в один слой.
5. `.../packaging.py:316-341` — сбор ассетов в output directory, обновление метаданных экспортированного mod-файла.
6. `.../packaging.py:343-349` — опциональная RTX IO постобработка: упаковка DDS и удаление исходных `.dds`.
7. `.../packaging.py:350-363` — отмена, очистка временных файлов, отчёт об ошибках и неудачных ассетах.
8. `lightspeed.trex.packaging.core/.../repair/core.py:66-148` — перед упаковкой проверяется, что слои не грязные, ассеты инжектированы и находятся в каталоге проекта; затем применяются repair-запросы.

---

## Что не описано

- UI-виджеты (деревья слоёв, панели packaging, job queue) — только их входные данные и команды.
- C++-плагины валидации/конвертации (`ConvertToDDS`, `ConvertToOctahedral`, RTX IO) доступны только по JSON-схемам и точкам вызова.
- `apply_handler.py`ComfyUI есть в исходном репозитории `F:/repos/toolkit-remix-1.6.1-0`, но отсутствует в установленном `O:\RTX Remix Toolkit`; Apply через ComfyUI здесь не разбирался.
- Игровой runtime Remix: как именно он читает `IsRemixRef`, `preserveOriginalDrawCall` и vertex colors — в исходниках Toolkit нет.
