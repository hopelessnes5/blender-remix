# Что делает плагин RTX Remix: по шагам

Документ составлен по исходникам `F:/repos/blender-remix/rtx_remix_importer/`. Ссылки вида `export_operator.py:1977` означают путь от этой папки. Это описание текущего кода, включая узкие места.

## Источники по разделам

| Раздел | Файлы |
|---|---|
| Регистрация, настройки, UI | `__init__.py`, `ui/__init__.py`, `ui/properties.py`, `ui/project_panel.py`, `ui/capture_panel.py`, `ui/material_panel.py`, `ui/asset_panel.py`, `ui/camera_menu.py` |
| Захваты | `ui/operators/capture_ops.py`, `ui/capture_list.py`, `thumbnail_dds.py`, `core_utils.py` |
| Импорт и Apply | `import_core.py`, `blender_bridge/*`, `legacy_import_core.py`, `ui/operators/sync_ops.py`, `ui/operators/mod_loader.py`, `mod_apply_utils.py`, `material_utils.py`, `light_utils.py`, `light_sharing.py` |
| Экспорт | `operators/export_operator.py`, `export_utils.py`, `glass_nodes.py`, `material_defaults.py`, `material_family.py`, `normal_export.py`, `scalar_texture.py`, `displacement.py`, `core_utils.py` |
| Утилиты и защита | `ui/operators/project_ops.py`, `hide_ops.py`, `asset_manager_ops.py`, `asset_ops.py`, `utility_ops.py`, `camera_ops.py`, `write_guard.py`, `nvidia_core/hide.py`, `mesh_uv_preservation.py`, `update_checker.py` |

---

## 1. Регистрация и настройки

1. `__init__.py:1-9` — `bl_info`: «Blender Remix Toolkit», версия 0.5.0, минимальный Blender 4.0.
2. `__init__.py:24-36` — `register()` печатает версию Blender, включает режим совместимости для Blender 4.1+, регистрирует `operators`, `ui`, `update_checker`, вешает `_cleanup_before_load` на `load_pre`.
3. `ui/__init__.py:66-129` — 21 оператор, 4 панели, меню камер, UIList захватов и PropertyGroup регистрируются в порядке: группы → свойства → операторы → UIList → панели → меню → превью; вешается `on_depsgraph_update`.
4. `ui/properties.py:626-804` — сценовые свойства: пути игры/мода/подслоёв, имя игры, якорный меш, масштаб экспорта, кодировка нормалей, папки мешей/текстур, список захватов.
5. `ui/properties.py:759-765` — смена папки захватов вызывает `remix.scan_capture_folder`; смена индекса списка грузит миниатюру (`properties.py:613-624`).
6. `export_operator.py:2458-2473` — оператор экспорта регистрирует `remix_inherit_parent_material` (по умолчанию `True`).
7. `ui/properties.py:713-717` — свойство `remix_reuse_existing_textures` объявлено, но в текущем коде экспорта не используется.
8. `__init__.py:38-45`, `ui/__init__.py:132-146`, `properties.py:806-883` — deregistration снимает обработчики, превью и классы; чистит ID-свойства сцены и кэши.

---

## 2. Настройка проекта и слои

1. `ui/properties.py:24-44, 47-68` — поиск папки `rtx-remix` в корне игры и до двух уровней ниже; оценка кандидатов по наличию `mods` и `captures`.
2. `ui/properties.py:71-92` — обход `mods` до глубины 4, поиск `mod.usda/.usd/.usdc`, список модов.
3. `ui/properties.py:99-170` — выбор мода проверяется через `os.path.commonpath`; при выборе вызывается `remix.load_project`.
4. `ui/properties.py:173-271` — сканирование папки игры: пути, имя игры, список захватов, восстановление последнего/сохранённого мода.
5. `ui/properties.py:294-604` — сохранённые локации игр в `saved_games.json` (CONFIG Blender): валидация 32-hex id, абсолютный путь, дедупликация, атомарная запись, меню переключения.
6. `ui/operators/project_ops.py:26-96` — анализ графа подслоёв: поиск циклов, capture-подслоёв, вставка подслоя relative-путём без дублей и циклов.
7. `ui/operators/project_ops.py:98-245` — `LoadRemixProject`: проверка файла, открытие stage, рекурсивный обход subLayerPaths, подключение корня `mod.usda` с depth=0, сохранение дерева в `scene["_remix_sublayers_ordered"]`, сброс активного подслоя.
8. `ui/operators/project_ops.py:247-276` — `SetTargetSublayer` через write guard записывает `remix_active_sublayer_path`.
9. `ui/operators/project_ops.py:278-510` — создание подслоя или добавление существующего: папка `<имя>/<имя>.usda`, метаданные Remix (0–100, 24 fps, metersPerUnit=1), ссылка в активный слой, перезагрузка проекта.
10. `ui/operators/project_ops.py:512-590` — создание `mod.usda`: запрет перезаписи, ось Z, метаданные, назначение активным и загрузка.
11. `ui/project_panel.py:6-205` — панель проекта: выбор мода, Create/Reload, дерево подслоёв, кнопки экспорта, Hide/Restore, список экспортированных ассетов.
12. `ui/project_panel.py:207-232` — настройки экспорта: масштаб, авто-применение трансформаций, кодировка нормалей, наследование материала, скрытие исходного меша, якорь, папки.

---

## 3. Браузер захватов

1. `ui/operators/capture_ops.py:29-107` — `ScanCaptureFolder`: только верхний уровень папки, расширения `.usd/.usda/.usdc`, фильтр `is_capture_file`, сортировка по времени, запись в `remix_captures`, загрузка миниатюры.
2. `ui/operators/capture_ops.py:110-138` — `ImportCaptureFile`: вызов `import_rtx_remix_usd_with_materials()` с флагами материалов/света и масштабом сцены; сохранение имени камеры.
3. `ui/operators/capture_ops.py:151-215` — очистка списка, переключение выбора, пакетный импорт всех/выбранных захватов.
4. `ui/capture_panel.py:14-201` — панель Captures: быстрый выбор проекта, список, настройки импорта, Clear Cache / Fix Broken Textures, превью, кнопки пакетного импорта (`:144-157`), выравнивание по камере (`:159-168`).
5. `ui/capture_panel.py:204-255` — превью: `previews.new()` «main», кэш по `(path, mtime_ns, size)`, декодирование DDS в иконку 256×256, подгрузка при depsgraph-обновлении.
6. `thumbnail_dds.py:11-119` — декодер DDS (DX10, DXT1/3/5, RGBA16F, RGBA8/BGRA8/BGRX8) и вписывание в квадрат 256×256.

---

## 4. Импорт захвата и Apply Mod

### 4.1 Импорт захвата

1. `ui/operators/capture_ops.py:29-107` — сканирование только верхнего уровня папки, фильтр `is_capture_file` (отбрасывает поассетные USD), сортировка по mtime.
2. `ui/operators/capture_ops.py:110-138` — импорт выбранного файла синхронно: так обходят краши Blender 5.0+ от depsgraph в фоновых потоках; имя первой камеры сохраняется в сцене.
3. `ui/operators/capture_ops.py:179-271` — пакетный импорт: временный `use_simplify`, импорт каждого файла, восстановление настроек в `finally`; ошибка одного файла не останавливает остальные.
4. `import_core.py:9-18, 112-136` — выбор бэкенда: `REMIX_IMPORT_BACKEND` → `scene["remix_import_backend"]` → `direct`; значения `direct`, `direct-only`, `legacy`.
5. `import_core.py:139-173` — facade: `legacy` сразу уходит в старый импортер; иначе создаётся `_DirectImportCheckpoint` и вызывается `import_with_direct_backend()`.
6. `blender_bridge/scene_builder.py:209-234` — коллекции Meshes/Lights/Cameras, `ProjectComposition.open()`, вычисление unit scale; в `finally` всегда `composition.close()`.
7. `nvidia_core/composition.py:148-241` — capture-слой блокируется в памяти, edit target ставится на mod-слой, upAxis и metersPerUnit читаются из capture.
8. `blender_bridge/scene_builder.py:34-83` — масштаб: UI-значение 0.01 означает «взять metersPerUnit из capture»; матрицы инстансов считаются по mapped-пути, а не по контейнеру прототипа.
9. `blender_bridge/scene_builder.py:90-171` — `_MeshRegistry` по путям/хэшам; обход `/RootNode/meshes`, более одного Mesh под прототипом → `UnsupportedGeometryError`.
10. `nvidia_core/geometry.py:220-323` — чтение points/counts/indices, проверка топологии, удаление вырожденных граней, n-гоны, UV/нормали/subsets.
11. `blender_bridge/mesh.py:14-64` — один Mesh ID на прототип: `from_pydata`, UV `st`, normal mode, проповки `usd_prim_path`/`remix_mesh_hash`/`source_layer`.
12. `blender_bridge/scene_builder.py:244-314` — объекты на инстансы с общим Mesh ID; материалы через `resolve_bound_material_path()` и `assign_material()`; если инстансов нет, прототипы импортируются напрямую.
13. `blender_bridge/scene_builder.py:316-347` — света и камеры; в сцену пишутся `source_capture_path`, `project_id`, `remix_import_backend`, `remix_import_unit_scale`.
14. `light_utils.py:20-347` — типы USD-светов в Blender, `energy = intensity·2^exposure·1000·scene_scale²`, один Light ID на группу одинаковых параметров.
15. `legacy_import_core.py:847-892` — старый путь: stage → контекст → коллекции → материалы/света/камеры/меши → finalize; при ошибке `(None, None, None, msg)`.
16. `import_core.py:58-109, 151-173` — откат direct-импорта: снимок ID/ключей/кэшей, `rollback()`, затем либо `USDImportError`, либо legacy-фолбэк. Это единственный полный rollback в плагине.

### 4.2 Apply Mod File Changes

17. `ui/operators/sync_ops.py:24-43, 151-215` — оператор доступен при загруженном моде; `execute()` открывает stage, строит object map, находит слои с правками и change-set, отключает обновления вью и заводит timer 0.01 c.
18. `ui/operators/sync_ops.py:88-138` — модальный цикл: по одному prim-пути за тик с бюджетом 5 мс; невалидный путь уходит в `_update_material_only()`; по завершении — cleanup, `_refresh_viewport()`, `FINISHED`.
19. `ui/operators/mod_loader.py:249-268` — object map по `usd_instance_path`, затем `usd_light_path`, затем `usd_prim_path`.
20. `ui/operators/mod_loader.py:366-418` — слои capture/capture_baker/data исключаются; в change-set попадают только authored `Def`-спеки.
21. `ui/operators/mod_loader.py:420-513` — `_get_changed_prims()`: только пути с 16-HEX хэшем сцены/capture или под `/Looks/`; шейдеры добавляют родительский материал.
22. `ui/operators/mod_loader.py:514-626` — сканирование material:binding и добавлений; найденные пути помечаются `_material_scan_prim_paths`, чтобы к ним не применялись трансформы.
23. `ui/operators/mod_loader.py:628-741` — маршрутизация: prototype light → mesh-контейнер → существующий объект или новый объект; material-only update пересоздаёт материал поверх текущего.
24. `ui/operators/mod_loader.py:1255-1525` — новые размещения и замена прототипа: части меша собираются в один Mesh ID, слоты по граням, joined UV/нормали, пустой `references=None` скрывает объекты.
25. `ui/operators/mod_loader.py:1578-1889` — замена геометрии по хэшу, копирование UV при идентичной топологии, особые случаи light-only прототипа.
26. `mod_apply_utils.py:628-1033` — материалы мода: поиск shader через surface output, семейство через `detect_nvidia_shader_family()`, переиспользование `*_mod_override`, patch входов, per-instance метаданные и glass-ветка.
27. `ui/operators/mod_loader.py:1088-1241` — Lights: по объекту на инстанс, общий Light ID по сигнатуре параметров, родитель-якорь, двойная фиксация `matrix_world` после обновления вью.
28. `mod_loader.py:851-896, 1892-1926` — коллекция `mod <имя>`, transform/visibility, работа с simplifier.
29. `sync_ops.py:217-221, 140-149` — общая ошибка даёт `CANCELLED`, `ESC` прерывает цикл; общего rollback всей сцены для Apply Mod нет.

---

## 5. Экспорт

### 5.1 Точки входа

1. `ui/project_panel.py:168-184` — кнопки «Export Selected Materials» (`material_replacement_mode=True`) и «Export Selected Meshes/Lights» (`False`); обе требуют активный подслой.
2. `export_operator.py:1977-2007` — `ExportRemixAsset` (`export_scene.rtx_remix_asset`) пишет в активный sublayer.
3. `export_operator.py:2242-2276` — `ExportRemixModFile` (`export_scene.rtx_remix_mod_file`) пишет прямо в `mod.usda`; `export_mesh` переиспользован от первого оператора.
4. `export_operator.py:2211-2238, 2278-2303` — `execute()` передаёт выделение и путь в `_export_remix_objects()`, отчитывается числом успехов/ошибок.

### 5.2 Подготовка

5. `export_operator.py:2067-2073` — `export_mesh()` читает масштаб, якорь, флаги `remix_hide_original_mesh` и `remix_inherit_parent_material`.
6. `export_operator.py:207-229` — `get_game_stage()` открывает и кэширует mod-файл только для чтения: там лежат capture-трансформы, которых нет в отдельном sublayer.
7. `export_operator.py:2009-2065` — `get_or_create_stage()` открывает слой через write guard или создаёт новый с метаданными Remix; для ingested-файла сразу `defaultPrim = /ReferenceTarget`.
8. `export_operator.py:1815-1843` — `_preflight_texture_destinations()` собирает карту планируемых DDS-путей без записи prim’ов, чтобы поймать конфликты до любой записи.
9. `export_operator.py:1087-1220` — `find_texture_for_socket()` сопоставляет сокеты, считает имя DDS, фиксирует источник;конфликт на один путь даёт `ValueError`.
10. `export_operator.py:1212-1215` — грязное изображение с уже существующим DDS прерывает экспорт до записи.
11. `export_operator.py:998-1029` — Normal Map с image-источником требуетStrength=1.0; height displacement несовместим с translucent.
12. `export_operator.py:1851-1878` — проверка пустого выделения, уникальности имён `.usda`, каталога проекта, запуск preflight и копирование `.mdl` в `assets/remix_materials`.

### 5.3 Новый экспорт меша

13. `export_operator.py:2067-2076` — `ExportRemixAsset.export_mesh()` делегирует в `mesh_export.export_mesh()`; старые функции authoring’а в `export_operator.py` больше не вызываются.
14. `mesh_export.py:65-89` — `bake_transforms()`: rotation и scale переносятся в mesh-data через `obj.data.transform()`, без `bpy.ops` и требования контекста 3D-вида; по умолчанию выключено (`properties.py:706-710`), так как импортированные capture-объекты несут поворот Y-up → Z-up, который должен оставаться на объекте.
15. `mesh_export.py:91-173` — `open_game_stage()`, поиск матрицы инстанса в capture-файлах и `resolve_target()`: цель ищется по тегам/хэшу, хэш из capture принимается даже без прима в текущем стейдже.
16. `mesh_export.py:174-233` — путь ассета, метаданные data-слоя, default fallback-материал `AperturePBR_Opacity.mdl`.
17. `mesh_export.py:235-360` — `write_asset()`: каркас `/ReferenceTarget/XForms`, `visibility = inherited` на корне/группе/меше, точки в локальном пространстве прототипа, extent, UV `st`, нормали `faceVarying`, `no_delete = true`; материал пишется только при снятой галочке наследования.
17a. `mesh_export.py:204-270` — сохранение vertex colors: `displayColor` (`color3f[]`) и `displayOpacity` (`float[]`) с `interpolation = vertex` берутся из color attributes Blender или из исходного USD-меша захвата (флаг `isVertexColorBakedLighting` означает запеченное освещение).
18. `mesh_export.py:363-431` — `_remove_spec()`, `_holder_for()` и `author_reference()`: для каждого экспортируемого объекта создаётся отдельный `ref_<uuid>`-холдер; повторный экспорт того же объекта переиспользует свой холдер (`obj["remix_reference_prim_path"]`); соседние `ref_*` не удаляются; есть переопределения на объект `remix_hide_original_mesh` / `remix_inherit_parent_material` с фолбэком на сцену; `hide_original` агрегируется по всем объектам на одном `mesh_path`; при коллизии базовых имён ассетов добавляется детерминированный суффикс.
19. `mesh_export.py:392-432` — `hide_original=True` даёт `over mesh_<HASH> (references = None)`; `False` — `over mesh_<HASH>` без маски, то есть добавление к целевому мешу с сохранением исходной геометрии. В обоих случаях на этом же prime мод-слой пишет `visibility = inherited`: capture часто держит `visibility = invisible`, и одного `inherited` внутри ассета недостаточно — USD поднимается к родителю и находит `invisible`.
20. `mesh_export.py:433-500` — `author_new_mesh()` и `export_mesh()`: относительный путь ассета, сохранение mod-слоя, флаги `remix_processed`/`remix_mesh_file_path`/`remix_material_path`.

### 5.4 Материал: наследование или экспорт из нод

21. `mesh_export.py:290-355` — при `remix_inherit_parent_material=True` группы полигонов собираются без материала: `material:binding` в ассете не пишется, замена наследует `mat_<HASH>` от capture-родителя.
22. `mesh_export.py:350-358` — при снятой галочке вызывается `export_material_fn()` (реальный writer из `export_operator.py:964`) с `material_scope_path=/ReferenceTarget/Looks` и биндом на part-прим;материал без текстур получает default material через `:207-233`.

### 5.5 Материал и текстуры

28. `export_operator.py:1032-1079` — `_export_material_impl()`: displacement, тип материала, normal strength, фаза планирования пути (`override_material_path` / `material_scope_path` / `/RootNode/Looks/mat_<uuid>`).
29. `export_utils.py:139-165` — `detect_material_type()`: Glass / legacy translucent / Principled с Transmission ≥ 0.5 → `AperturePBR_Translucent`, иначе `AperturePBR_Opacity`.
30. `export_operator.py:1081-1301` — discovery сокетов: diffuse/metallic/roughness/normal, emission, alpha, specular, height; только plan без conversions.
31. `export_operator.py:1338-1407` — authoring prim’ов, MDL-триада `info:*`, выходы surface/displacement/volume; при замене триада меняется только при смене семейства.
32. `export_operator.py:1409-1534` — параметры translucent и opacity, константы, volume-авторитет через `glass_nodes.volume_transmittance()` (`1442-1457`).
33. `glass_nodes.py:113-167` — чтение объёма: `VOLUME_ABSORPTION` σ = density·(1−color), `PRINCIPLED_VOLUME` σ = density·(1−sqrt(Absorption Color)), рассеяние отклоняется.
34. `export_operator.py:1535-1587`, `material_defaults.py:66-110` — маскировка незаписанных managed-входов и мёртвых Inputs после смены семейства, удаление MDL-дефолтов только при известной слабой композиции.
35. `export_operator.py:910-995` — `_MaterialTextureTransaction`: DDS сначала во временную папку, `publish()` через `os.replace`, `rollback()` восстанавливает старые файлы.
36. `export_operator.py:1303-1328` — conversions до первой записи prim’ов: normal, scalar/height, пакет остальных.
37. `core_utils.py:652-872` — `TextureProcessor`: `is_source_target()` (realpath + samefile), форматы и суффиксы `.a/.n/.r/.m/.e/.o/.s.rtex`, поиск texconv.
38. `core_utils.py:876-1121` — package conversion: временные PNG на главном потоке, один texconv на группу, публикация через gate; очередь с таймаутами и отменой.
39. `normal_export.py:1-23`, `scalar_texture.py:4-53`, `displacement.py:11-162` — четыре режима кодировки нормалей, чтение scalar-каналов, чтение/запись displacement.

### 5.6 Свет и устаревший путь

40. `export_operator.py:1592-1600` — `export_light()` делегирует в `light_export.export_light()`.
41. `light_export.py:49-67` — уникальное имя на объект: stored `usd_light_path` переиспользуется, только если его не разделяют другие света сцены; иначе детерминированное `light_<16HEX>` из имени Blender-объекта. Это убирает слияние одинаковых копий в один prim.
42. `light_export.py:70-79, 111-125` — температура: `inputs:enableColorTemperature` (bool) и `inputs:colorTemperature` (float, K, clamp 1000–10000) из `use_temperature`/`temperature` Blender. При включённой температуре `inputs:color` пишется белым `(1,1,1)`: Remix умножает blackbody на базовый цвет, поэтому выбор цвета в конфиге не нужен; при выключенной температуре пишется `bl_light.color`.
43. `light_export.py:113-123` — `inputs:intensity = bl_light.energy` 1:1, без формул и без множителей; `remix_export_scale` влияет только на геометрию (радиусы/размеры), не на яркость; `inputs:exposure = 0`.
44. `light_export.py:103-...` — типы POINT/SPOT/SUN/AREA, радиус/shaping, extent, parent по якорю или `/RootNode/remix_assets`, проверка TRS, transform, биндинг материала и сохранение слоя; каждый свет записывает свой `def <LightType>` prim.
45. `light_utils.py:106-119` — импорт зеркален экспорту: `energy = intensity × 2^exposure` 1:1, без `×1000` и без `scene_scale²`; `mod_apply_utils.py:185`, `mod_loader.py:2025`, `sync_ops.py:428` уже использовали это правило.
46. `export_utils.py:20-385, 389-874` — устаревшие `MDLManager`, `ShaderSetup`, `MaterialExporter`, `LightExporter`, `TextureExporter`; основной оператор их не вызывает, но `_export_material_textures()` (`:453-679`) дублирует отображения и volume-override.

---

## 6. Скрытие и восстановление захваченных мешей

1. `ui/operators/hide_ops.py:51-158` — общий `_MeshMaskOperator`: поиск прототипа `mesh_<HASH>`, открытие mod-слоя через write guard, применение к каждому уникальному мешу, сохранение, инвалидация кэша game stage.
2. `ui/operators/hide_ops.py:161-186` — Hide (`object.rtx_remix_hide_mesh`) и Show (`object.rtx_remix_show_mesh`, с восстановлением legacy-инстансов).
3. `nvidia_core/hide.py:51-94` — проверка абсолютных путей по regex mesh/inst и защита mod-слоя (capture и locked отклоняются).
4. `nvidia_core/hide.py:97-146` — `hide_mesh()` создаёт `over` и `references = None`; `is_mesh_hidden()` определяет маску по пустому explicit reference.
5. `nvidia_core/hide.py:149-179` — `unhide_mesh()` снимает правки reference и удаляет опустевший `over`-прим.

---

## 7. Экспортированные ассеты

1. `ui/operators/asset_manager_ops.py:16-246` — `ScanExportedAssets`: обход mod.usda и подслоёв, поиск мешей иLight-примов, сопоставление с объектами сцены, результат в `scene["_remix_exported_assets"]`.
2. `ui/operators/asset_manager_ops.py:249-375` — удаление prim spec’а из слоя через write guard и `Sdf.ChangeBlock()`, затем повторное сканирование.
3. `ui/operators/asset_manager_ops.py:378-435` — выделение объекта по имени (включая замену `_` → `.`).
4. `ui/project_panel.py:234-334` — список до 50 ассетов со счётчиками и кнопками select/delete.
5. `ui/operators/asset_ops.py:10-73` — `InvalidateRemixSingleAsset`: сброс `remix_processed` и удаление `remix_material_path`.
6. `ui/asset_panel.py:4-54` — панель обработанных ассетов с кнопками select/invalidate.

---

## 8. Утилиты

1. `ui/operators/utility_ops.py:5-31` — `ClearMaterialCache`: очистка кэша материалов/текстур и удаление дубликатов.
2. `ui/operators/utility_ops.py:34-192` — `FixBrokenTextures`: поиск `.dds` в `<capture>/textures`, конвертация, подмена изображений в TEX_IMAGE-нодах.
3. `ui/operators/utility_ops.py:195-389` — `ConvertModDDSToPNG`: сбор DDS-путей из узлов, конвертация в `<mod>/converted_textures`, подмена с сохранением colorspace.
4. `ui/operators/camera_ops.py:4-60` — `AlignViewToCamera`: PERSP, копирование позиции и поворота камеры в вид.
5. `ui/camera_menu.py:3-34` — меню импортированных Remix-камер с подписью источника.
6. `mesh_uv_preservation.py:9-65` — сравнение топологии и UV, копирование UV только при совпадении.

---

## 9. Защита записи и жизненный цикл

1. `write_guard.py:1-94` — единый guard: `layer_type()`, `is_capture_layer()`, `ensure_writable_layer()`, `open_writable_layer()`, `ensure_editable_edit_target()`. Capture-слой и слой с `permissionToEdit=False` не пишутся; проверка повторяется прямо перед сохранением.
2. `nvidia_core/hide.py:83-94` — те же правила для скрытия.
3. `__init__.py:19-22, 35-45` — очистка ресурсов перед загрузкой .blend и при unregister.
4. `ui/__init__.py:132-146` — снятие depsgraph-обработчика, превью и классов.
5. `update_checker.py:32-199` — проверка обновлений через GitHub API в daemon-потоке, сравнение версий, опрос результата app-timer’ом на главном потоке, окно Preferences с кнопкой Check for Updates.

---

## Ограничения текущего кода

- `remix_reuse_existing_textures` (`properties.py:713-717`) зарегистрирован, но не используется.
- Старые функции меш-экспорта (`author_mesh_replacement`, `write_ingested_mesh_asset` и др.) остались в `export_operator.py`, но новый оператор их не вызывает.
- Главный путь экспорта использует синхронный `_convert_texture_batch_sync`; асинхронный `MaterialExporter` в `export_utils.py` остался как отдельный путь.
- Игровой RTX Remix runtime не проверялся; документ описывает только код Blender-аддона.
