# Аудит Remix Toolkit for Blender

> **Исторический снимок.** Аудит выполнен на указанном ниже коммите. После него внесены исправления: проверка capture при открытии экспортного слоя и части UI-операций, защита исходной normal DDS, выбор активного шейдера, корректировка индексов граней/primvar и чтение стандартных нормалей. Эти изменения могут быть незакоммиченными; их полноту и оставшиеся пути обязательно проверять по текущему дереву. Агенты не должны автоматически считать другие пункты исправленными или переносить диагностические предположения в код без проверки.

Дата: 1 ноября 2026. Репозиторий: `F:/repos/blender-remix`, ветка `dev`, коммит `bbfd5632a20e0dd58bf939243bdc50cdd4065844`.

## Результат и границы

Обнаружено 28 замечаний: 17 с приоритетом P1 и 11 с приоритетом P2. P1 — риск изменения исходных данных, нестабильности Blender или неверного основного результата импорта/экспорта; P2 — ошибка при определённых входных данных или неполнота поддерживаемого сценария. Безусловных P0, проявляющихся при каждом запуске, не установлено.

Это аудит текущего проекта, а не только последнего diff. Проверены архитектура и основные пути исполнения всех подсистем: direct/legacy import, geometry bridge, материалы, текстуры, USD composition, экспорт мешей и света, применение мода, UI операций со слоями/ассетами, thumbnails, очистка суффиксов, регистрация и тестовая инфраструктура. Синтаксически проверены все 78 Python-файлов, включая тесты и tools; в аддоне 53 Python-файла, 17 800 строк.

Полный набор из 155 тестов проходит в Blender 4.5.5 LTS и 5.1.2. Дополнительно выполнены 17 диагностических сценариев в обеих версиях: их наблюдения подтверждают конкретные ошибки, отсутствующие в существующих тестах. Диагностические сценарии используют искусственные данные и местами подменяют вызов конвертера/фабрику материалов для изоляции причины. Они не являются 17 дополнительными успешно пройденными регрессионными тестами: зафиксированное неправильное поведение ещё предстоит исправить.

Исходный репозиторий не изменялся. Тестирование проводилось в копии. Не запускались игра/RTX Remix runtime, интерактивный рендер, реальные пользовательские моды, стресс-тест многогигабайтных захватов или проверка всех поддерживаемых версий Blender. Полный аудит исходников и доступных сценариев не доказывает отсутствие иных ошибок.

Обозначения доказательств: **Воспроизведено** — есть запись в `audit-evidence.json`; **По коду** — прослежен путь исполнения, но отдельный запуск данного сценария не проводился. Источники и строки относятся к указанному коммиту.

## P1: безопасность записи и надёжность экспорта

### A01. Захват принимается как целевой слой экспорта

**Воспроизведено:** `capture_export_guard`.

`ExportRemixAsset.get_or_create_stage()` открывает любой существующий USD без проверки `lightspeed_layer_type`. `SetTargetSublayer` принимает произвольный путь; загрузчик проекта перечисляет capture среди подслоёв. Защита `ProjectComposition` действует только на соответствующем пути импорта и не заменяет проверку в каждом месте записи. Синтетический capture был принят, `permissionToEdit=True`.

Места: [export_operator.py:1342](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:1342), [project_ops.py:178](F:/repos/blender-remix/rtx_remix_importer/ui/operators/project_ops.py:178), [asset_manager_ops.py:300](F:/repos/blender-remix/rtx_remix_importer/ui/operators/asset_manager_ops.py:300).

Исправление: единый валидатор разрешённого слоя записи, используемый экспортом, удалением ассетов, созданием/добавлением подслоёв. Проверять capture по метаданным и источнику, разрешения слоя, принадлежность выбранному проекту; повторять проверку перед фиксацией. Отрицательный тест должен проверять неизменность байтов capture. Не полагаться на то, что его ранее импортировали и заблокировали в памяти.

### A02. Конвертация нормали перезаписывает её исходный DDS

**Воспроизведено:** `normal_source_overwrite`.

Если `Image.filepath` совпадает с конечным DDS, `os.replace()` заменяет исходник. Экспорт OpenGL → Octahedral изменил файл, одновременно являвшийся источником изображения, и сообщил успех. Защита есть в экспорте высоты, но отсутствует у нормалей.

Место: [core_utils.py:592](F:/repos/blender-remix/rtx_remix_importer/core_utils.py:592), финальная замена — строка 630.

Исправление: общий контроль совпадения реальных путей источника и назначения, с учётом регистра Windows и существующих ссылок; либо безопасное повторное использование доказанно подходящего DDS, либо понятный отказ до записи. Не заменять эту проверку одним сравнением имён.

### A03. Фоновые задачи вызывают API Blender и меняют общую сцену

**По коду.** `BackgroundTextureProcessor` передаёт `bpy.types.Image` в `threading.Thread`. В потоке `_convert_texture_batch_sync()` вызывает `_save_blender_image_to_file()`: меняются `bl_image.file_format`, `scene.render.image_settings`, выполняется `save_render`. Несколько задач могут одновременно менять одни настройки. `ModFileLoader` также вызывает `bpy.data.images.load` из `ThreadPoolExecutor`.

Места: [core_utils.py:1377](F:/repos/blender-remix/rtx_remix_importer/core_utils.py:1377), [core_utils.py:1132](F:/repos/blender-remix/rtx_remix_importer/core_utils.py:1132), [mod_loader.py:236](F:/repos/blender-remix/rtx_remix_importer/ui/operators/mod_loader.py:236).

Это ограничение подтверждается [документацией Blender](https://docs.blender.org/api/5.0/info_gotchas_threading.html). Сам сбой процесса в ходе аудита намеренно не провоцировался.

Исправление: чтение/создание/сохранение Blender datablocks в главном потоке; фон получает только подготовленные файлы/буферы и обычные значения. Завершение применяется через main-thread timer/modal. Для каждого задания изолировать настройки и временные данные.

### A04. Успех экспорта не зависит от успешного создания всех текстур

**Воспроизведено частично:** `material_failure`; остальные ветви — по коду.

При возврате `False` из `export_normal_map()` материал возвращается как успешно экспортированный, но нормаль отсутствует. Остальные карты уходят в фон, а материал и USD уже считаются готовыми до завершения DDS. Фоновая задача получает статус `completed` даже если в `results` есть `False`. Ошибка texconv может оставить успешный на вид мод с отсутствующими текстурами.

Места: [export_operator.py:880](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:880), [export_operator.py:954](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:954), [core_utils.py:1430](F:/repos/blender-remix/rtx_remix_importer/core_utils.py:1430).

Исправление: завершение DDS — обязательная часть операции; ошибки/таймаут/отмена доходят до оператора и его итогового статуса. Не публиковать новую ссылку в моде до появления проверенного файла. Тесты: texconv отсутствует, вернул ошибку, не создал файл, завис, одна карта из нескольких не обработана.

### A05. Неуспешная операция может сохранить частичные USD-правки

**По коду.** `export_material()` создаёт prim/атрибуты до проверок конфликтов высоты и выполнения конвертации. `export_material_replacement()` ловит исключение и возвращает `False`; общий цикл всё равно сохраняет dirty root layer. Аналогичная проблема возможна при записи меша/текстур, завершившейся только частично.

Места: [export_operator.py:732](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:732), [export_operator.py:942](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:942), [export_operator.py:1298](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:1298).

Исправление: сначала валидировать и подготовить операцию, затем выполнить её в изолированном staging layer/наборе временных файлов и зафиксировать. При сбое сохранять исходные layer specs и файлы. `Sdf.ChangeBlock` сам по себе откат не обеспечивает. Успешные элементы пакетной операции могут сохраняться отдельно, но провалившийся элемент не должен оставлять часть своих правок.

## P1: материалы и текстуры

### A06. Сброс параметра до MDL-default не перекрывает capture

**Воспроизведено:** `default_reset_against_capture`.

`remove_default_inputs()` решает, нужно ли оставлять default override, по stage, открытой отдельно из целевого подслоя. Capture/родительский mod в этом stage отсутствует. Воспроизведение: capture roughness=0.8, Blender=0.5. Экспорт удалил 0.5 как default; итоговая композиция сохранила 0.8.

Место: [material_defaults.py:43](F:/repos/blender-remix/rtx_remix_importer/material_defaults.py:43), открытие отдельного target — [export_operator.py:1270](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:1270).

Исправление: принимать решение в полной композиции с правильным edit target либо явно передавать более слабое эффективное значение. Критерий: default опускается только тогда, когда итоговая композиция действительно остаётся равной желаемому значению Blender.

### A07. Повторный экспорт не удаляет устаревшие карты и включённые функции

**Воспроизведено:** `material_stale`.

Экспорт дописывает существующий Shader. После удаления карт normal/height и отключения opacity старые `normalmap_texture`, `height_texture`, `enable_opacity=True` сохраняются. По той же схеме могут оставаться emission/specular и параметры другой семьи материала. Если старое значение приходит из capture, простого удаления свойства из mod также недостаточно — требуется корректное перекрытие.

Места: [export_operator.py:999](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:999), [export_operator.py:1044](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:1044).

Исправление: вычислять полное желаемое состояние управляемых свойств и согласовывать его с существующим. Для clear/disable в слабых слоях применять правильное USD-представление. Сохранять чужие свойства и не чистить весь Shader без разбора. Тесты должны включать два последовательных экспорта с удалением каждого слота и переключением opaque/translucent.

### A08. Экспорт выбирает первый BSDF, включая отключённый

**Воспроизведено:** `material_active`.

Экспортёр перебирает все nodes и берёт первый Principled/подходящую group. В тесте отключённый BSDF имел roughness=0.1, подключённый к Material Output — 0.9; экспортировалось 0.1. Детектор типа материала и вспомогательные анализаторы имеют аналогичную модель поиска.

Места: [export_operator.py:770](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:770), [export_utils.py:136](F:/repos/blender-remix/rtx_remix_importer/export_utils.py:136).

Исправление: начать обход от active Material Output нужного target и его Surface. Поддержать оговорённые reroute/legacy group. Mix Shader, несколько шейдеров и неподдерживаемые графы должны обрабатываться явно, а не выбором случайного первого узла.

### A09. Импорт скалярных карт подключает Alpha вместо значения карты

**Воспроизведено:** `scalar_texture_import_channel`.

В `process_input()` для non-color карты и входа VALUE выбирается Alpha. Поэтому обычная grayscale roughness с alpha=1 становится постоянной 1; то же относится к metallic и отдельной opacity-карте. Наличие Alpha-сокета у изображения не означает, что нужные данные хранятся в нём.

Место: [material_utils.py:322](F:/repos/blender-remix/rtx_remix_importer/material_utils.py:322). Проверить аналогичное поведение в `mod_apply_utils.process_mod_input_util`.

Исправление: выбирать канал по семантике USD-слота и документированной схеме упаковки. Для BC4/grayscale использовать скалярные данные Color/R; albedo alpha — отдельный явный сценарий. Проверка численного значения в шейдере нужна дополнительно к проверке существования ссылки.

### A10. Экспорт теряет выбранный канал и обработку текстуры в графе

**Воспроизведено для Alpha:** `alpha_output_export_channel`; Math/Invert/ColorRamp — по коду.

Поиск источника возвращает только Image, игнорируя выход Alpha/R/G/B, промежуточные операции и выбор ветви. Отдельное изображение с RGB=0.2, Alpha=0.8, подключённое через Alpha к прозрачности, экспортировалось в BC4 примерно как 0.247, вместо 0.8. Операции над roughness и Strength ноды Normal Map также не представляются полученным сырым изображением.

Места: [export_operator.py:845](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:845), [export_operator.py:935](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:935), [export_utils.py:340](F:/repos/blender-remix/rtx_remix_importer/export_utils.py:340).

Исправление: анализатор возвращает image + канал + поддерживаемую операцию/конвенцию. Ввести явную границу поддерживаемого графа: bake либо понятная ошибка для остального. Не требуется реализовывать произвольный Blender shader graph в рамках первого исправления. Для scalar экспорта избегать цветовых/тональных преобразований `save_render`.

### A11. При импорте не учитывается `inputs:encoding` нормали

**По коду.** USD normalmap подключается к обычной ноде Blender Normal Map; `encoding` не читается в material/mod apply. Octahedral RG требует декодирования для Blender, DirectX — согласования оси Y. При повторном экспорте default OCT_CONVERT способен повторно преобразовать уже закодированные данные.

Места: [material_utils.py:330](F:/repos/blender-remix/rtx_remix_importer/material_utils.py:330), [material_utils.py:400](F:/repos/blender-remix/rtx_remix_importer/material_utils.py:400), [normal_export.py:5](F:/repos/blender-remix/rtx_remix_importer/normal_export.py:5).

Исправление: сохранять происхождение и кодировку normal map, создавать правильное Blender-представление, согласовать её с пользовательским default экспорта. Нулевой B не является достаточным признаком octahedral. Проверять направления декодированных векторов и цикл импорт → экспорт, а не визуальную читаемость одного канала.

### A12. DDS fallback оставляет непакованное изображение с удалённым источником

**Воспроизведено:** `dds_fallback_persistence`.

`_convert_dds_to_png()` загружает временный PNG и удаляет его в finally. Возвращённый Image имеет `source='FILE'`, `packed_file=None`, а его filepath уже не существует. До перезапуска пиксели могут быть доступны в памяти; после сохранения/открытия .blend или reload текстура теряется.

Место: [texture_loader.py:170](F:/repos/blender-remix/rtx_remix_importer/texture_loader.py:170).

Исправление: до удаления временного файла упаковать изображение или создать устойчивое представление с сохранённым исходным DDS path. Не возвращать временный путь как постоянный источник. Тест: сохранить .blend, открыть в новом процессе Blender, сравнить пиксели.

### A13. Повторный экспорт обычных DDS пропускает изменившееся изображение — P2

**По коду.** Любой существующий output DDS считается готовым, независимо от source/пикселей/формата. После Texture Paint, reload или замены изображения с тем же экспортным именем повторный экспорт оставит старые данные. Поведение описано как reuse, но проверки пригодности файла нет.

Места: [export_operator.py:885](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:885), [core_utils.py:743](F:/repos/blender-remix/rtx_remix_importer/core_utils.py:743).

Исправление: либо всегда пересоздавать результат, либо использовать доказуемую идентичность содержимого и настроек. Проверять и одиночный, и batch путь. Не добавлять суффиксы в экспортные имена ради кэша.

### A14. Конфликты имён текстур не проверяются между материалами

**По коду.** `texture_destinations={}` локален одному вызову `export_material()`. Изображения `wall.png` и `wall.jpg` дают один `wall.dds`; другой материал не знает о предыдущем назначении. Обычная карта может переиспользовать чужой файл, а нормаль/высота — перезаписать его. Несколько фоновых jobs усиливают гонку.

Места: [export_operator.py:790](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:790), [core_utils.py:580](F:/repos/blender-remix/rtx_remix_importer/core_utils.py:580).

Исправление: единый план назначений на всю экспортную операцию, с учётом роли/канала/кодировки, нормализации путей и уже существующих зависимостей. Разное содержимое в один путь должно давать явный конфликт до записи. Сохранить имена текстур из Image-ноды без автоматически добавляемых `.rtex` и hash-суффиксов.

## Геометрия, инстансы и применение мода

### A15. После вырожденной грани геометрия собирается из неверных индексов — P1

**Воспроизведено:** `degenerate_face_offsets`.

`extract_mesh_data()` сохраняет исходный массив indices, но отфильтрованный counts. `MeshData.polygons()` читает их с нулевого смещения. Для counts=[2,3], indices=[0,1,2,3,4] правильная оставшаяся грань [2,3,4] превратилась в [0,1,2]. UV/normal corner ordering при этом использует другие offsets.

Место: [geometry.py:118](F:/repos/blender-remix/rtx_remix_importer/nvidia_core/geometry.py:118).

Исправление: единая модель исходных и отфильтрованных corner offsets для topology/UV/normals/subsets. Проверять некорректные counts и выходящие за диапазон индексы до создания Blender mesh.

### A16. Direct import не находит вложенную геометрию собственного экспорта — P1

**Воспроизведено:** `own_export_nested_mesh_lookup`.

`find_mesh_prim()` проверяет только сам prototype и его непосредственных детей. Экспорт использует `ref_.../XForms/<mesh>`; после композиции Mesh может оказаться глубже и не будет найден. Кроме того, регистр хранит один mesh на prototype, а матрица объекта берётся с instance/prototype: локальный transform найденного дочернего Mesh не переносится отдельно.

Места: [geometry.py:218](F:/repos/blender-remix/rtx_remix_importer/nvidia_core/geometry.py:218), [scene_builder.py:106](F:/repos/blender-remix/rtx_remix_importer/blender_bridge/scene_builder.py:106), [scene_builder.py:226](F:/repos/blender-remix/rtx_remix_importer/blender_bridge/scene_builder.py:226).

Исправление: обход составной геометрии с сохранением локальных transform, нескольких Mesh и instance/prototype provenance. Минимальный критерий — мод, экспортированный аддоном, открывается его direct importer без пропавшей/сдвинутой геометрии.

### A17. Индексы primvar применяются только к faceVarying — P2

**Воспроизведено:** `indexed_vertex_uv`.

`InterpolatedAttribute.resolve()` учитывает `indices` только для faceVarying. Для indexed vertex UV с index[0]=2 возвращается value[0] вместо value[2]. Аналогично требуется проверить uniform/constant и varying.

Место: [geometry.py:64](F:/repos/blender-remix/rtx_remix_importer/nvidia_core/geometry.py:64).

Исправление: сначала вычислять элемент по interpolation, затем единообразно применять индексацию. Тесты должны включать непоследовательные индексы и разную длину values/indices.

### A18. Стандартные USD mesh normals игнорируются — P2

**Воспроизведено:** `standard_usd_normals`.

Импорт читает только `primvars:normals`. Собственный экспорт использует `UsdGeom.Mesh.CreateNormalsAttr()`, то есть стандартный атрибут `normals`. В тесте authored faceVarying normals не прочитаны; Blender пересчитывает их и может изменить сглаживание.

Места: [geometry.py:280](F:/repos/blender-remix/rtx_remix_importer/nvidia_core/geometry.py:280), [export_operator.py:628](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:628).

Исправление: поддержать normals attribute с его interpolation и предусмотренную проектом primvar-форму, с явным приоритетом при наличии обеих. Проверять roundtrip custom normals численно.

### A19. Материалы граней и дополнительные material slots теряются — P2

**По коду.** `SubsetData` извлекается, но bridge не применяет material indices. Поиск материала возвращает первый найденный Mesh/Subset binding, `assign_material()` назначает только slot 0. Экспорт также берёт `obj.material_slots[0]`, не создавая GeomSubset для остальных полигонов.

Места: [blender_bridge/materials.py:93](F:/repos/blender-remix/rtx_remix_importer/blender_bridge/materials.py:93), [blender_bridge/mesh.py:26](F:/repos/blender-remix/rtx_remix_importer/blender_bridge/mesh.py:26), [export_operator.py:1446](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:1446).

Исправление: либо реализовать mapping materialBind subsets ↔ polygon.material_index, либо явный отказ/предупреждение до экспорта многоматериального меша. Молчаливый успех с одним материалом неприемлем.

### A20. Apply Mod обновляет mesh slot вместо object-linked материала — P1

**Воспроизведено:** `apply_mod_object_linked_material`.

Direct importer устанавливает `slot.link='OBJECT'`, чтобы разные инстансы общего mesh имели разные материалы. `ModFileLoader` читает и пишет `bl_object.data.materials[0]`. В тесте mesh slot стал UpdatedOverride, а реально используемый object slot остался ObjectOverride. Обновление также может затронуть другие объекты с тем же Mesh datablock.

Места: [blender_bridge/materials.py:144](F:/repos/blender-remix/rtx_remix_importer/blender_bridge/materials.py:144), [mod_loader.py:606](F:/repos/blender-remix/rtx_remix_importer/ui/operators/mod_loader.py:606), [mod_loader.py:624](F:/repos/blender-remix/rtx_remix_importer/ui/operators/mod_loader.py:624), аналогичный путь — строка 498.

Исправление: читать эффективный material slot объекта и сохранять object-linked семантику. Тестировать два инстанса одного Mesh с разными overrides и независимое изменение одного из них.

## Свет и камера

### A21. Импорт и экспорт света не являются обратными преобразованиями — P1

**Воспроизведено:** `light_import_export_units`.

Импорт location/radius масштабирует через scene_scale; экспорт location снова умножает на export_scale, тогда как mesh export делит. Размеры света и energy также не возвращаются в исходные единицы. В тесте scale=0.01: X 100 → 0.01, radius 1 → 0.01, intensity 100 → 10 после полного цикла. Это численные наблюдения текущего кода, а не заключение о физической фотометрии Remix.

Места: [light_utils.py:55](F:/repos/blender-remix/rtx_remix_importer/light_utils.py:55), [light_utils.py:85](F:/repos/blender-remix/rtx_remix_importer/light_utils.py:85), [export_operator.py:1087](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:1087), [export_operator.py:1224](F:/repos/blender-remix/rtx_remix_importer/operators/export_operator.py:1224).

Исправление: единая документированная модель единиц для position, radius/width/height, intensity/exposure и angular parameters. Сначала обеспечить обратимость поддерживаемого представления, затем сверить фотометрию с Toolkit/runtime. Проверять POINT/AREA/SPOT/SUN отдельно.

### A22. Y-up свет получает преобразованную позицию, но прежний quaternion — P2

**По коду.** В Y-up ветви position преобразуется, а rotation остаётся исходным; код сам печатает предупреждение. Rect/Disk/Spot могут светить не в ту сторону. Direct meshes/cameras используют отдельное преобразование матрицы.

Место: [light_utils.py:44](F:/repos/blender-remix/rtx_remix_importer/light_utils.py:44).

Исправление: общий matrix-based USD→Blender basis transform, согласованный с capture axis и импортом геометрии. Проверить направленный свет с неединичной исходной rotation.

### A23. Clipping камеры не переводится в единицы Blender — P2

**По коду.** Матрица камеры масштабируется через unit_scale, но `clippingRange` присваивается напрямую в `clip_start/end`. Для сантиметровой сцены расстояния клиппинга отличаются от геометрии в 100 раз.

Место: [scene_builder.py:154](F:/repos/blender-remix/rtx_remix_importer/blender_bridge/scene_builder.py:154).

Исправление: масштабировать расстояния clip; не применять эту же формулу автоматически к focalLength/aperture, у которых иная USD-семантика единиц. Добавить тест сцены с известными плоскостями отсечения.

## Остальные сценарии и жизненный цикл

### A24. Очистка дубликатов удаляет разные изображения по одному имени — P2

**Воспроизведено:** `duplicate_cleanup_different_pixels`.

`cleanup_duplicate_textures()` считает image.001 дублем image без проверки пути, packed/generated content, colorspace и dirty pixels. В тесте зелёное изображение было заменено красным, затем удалено. После удаления код обращается к `image.name`, получая ReferenceError; обработчик исключения снова читает удалённый image и выбрасывает ошибку наружу.

Места: [texture_loader.py:260](F:/repos/blender-remix/rtx_remix_importer/texture_loader.py:260), похожий fallback — [texture_cleanup_operator.py:30](F:/repos/blender-remix/rtx_remix_importer/operators/texture_cleanup_operator.py:30).

Снижение приоритета: модуль texture_cleanup_operator не подключён в текущем `operators.register()`, поэтому его отдельная кнопка не является гарантированно доступным пользовательским путём. Ошибка самой функции подтверждена.

Исправление: доказательство идентичности перед remap; не объединять разные interpretation/colorspace; имя сохранить до удаления. При непроверяемой идентичности пропускать изображение с понятным результатом.

### A25. Добавление подслоя допускает циклы и не учитывает порядок силы — P2

**По коду.** Add Existing Sublayer не запрещает добавить текущий файл в себя или создать косвенный цикл. Новые replacement layers безусловно добавляются через append, то есть в конец, в том числе после capture. При такой композиции capture сильнее нового replacement, и ожидаемые правки могут не проявиться.

Места: [project_ops.py:325](F:/repos/blender-remix/rtx_remix_importer/ui/operators/project_ops.py:325), [project_ops.py:408](F:/repos/blender-remix/rtx_remix_importer/ui/operators/project_ops.py:408).

Исправление: preflight графа зависимостей, отсутствие циклов/дубликатов, capture остаётся самым слабым, новые edits имеют определённое положение в стеке. Операции записи должны использовать защиту A01. Дерево UI не должно предлагать capture как редактируемую цель.

### A26. Отключение аддона не завершает фоновые jobs и их timer — P2

**По коду.** Root unregister снимает UI и export classes; `cleanup_addon_resources()` не вызывается. Отдельный BackgroundTextureProcessor со своими daemon threads и persistent timer вообще не включён в эту очистку. Работа может продолжаться после disable/reload, с устаревшими callbacks и Image references.

Места: [__init__.py:27](F:/repos/blender-remix/rtx_remix_importer/__init__.py:27), [core_utils.py:1338](F:/repos/blender-remix/rtx_remix_importer/core_utils.py:1338), [core_utils.py:1384](F:/repos/blender-remix/rtx_remix_importer/core_utils.py:1384).

Исправление: единый lifecycle задач, отмена без публикации частичных файлов, корректное снятие timers, очистка caches, безопасное завершение subprocesses, idempotent disable/enable. Не вызывать неограниченный join в UI-потоке.

### A27. Direct→legacy fallback не откатывает частично импортированную сцену — P2

**По коду.** Direct importer создаёт коллекции, mesh datablocks и объекты до окончания импорта. finally закрывает только composition. Если позднее возникает исключение, facade запускает legacy в уже изменённой сцене. Возможны дубликаты и остатки неуспешного импорта. Некоторые исключения поглощаются на уровне prototype/material и дополнительно усложняют определение результата.

Места: [scene_builder.py:180](F:/repos/blender-remix/rtx_remix_importer/blender_bridge/scene_builder.py:180), [scene_builder.py:300](F:/repos/blender-remix/rtx_remix_importer/blender_bridge/scene_builder.py:300), [import_core.py:103](F:/repos/blender-remix/rtx_remix_importer/import_core.py:103).

Исправление: учёт созданных объектов/datablocks/collections и откат только своего импорта перед fallback. Пользовательская сцена и существующие shared materials сохраняются. Тест — искусственная ошибка после первого созданного instance.

### A28. Пустой material binding обрушает сбор хэшей capture — P2

**Воспроизведено:** `capture_hashes_empty_binding`.

`get_hashes_from_capture_layer()` проверяет наличие relationship, затем без проверки берёт explicitItems[0]. Пустая явная привязка и только prepended target приводят к IndexError. Воспроизведено для пустой привязки. Существование relationship не гарантирует наличие target.

Место: [capture.py:168](F:/repos/blender-remix/rtx_remix_importer/nvidia_core/capture.py:168).

Исправление: определить поддерживаемую семантику Sdf target list ops, проверять пустые/удалённые/несуществующие targets; сохранять mesh hash даже при отсутствии материала. Добавить отрицательные unit tests.

## Технический долг и проверки до расширения функциональности

Эти пункты не включены в число 28 подтверждённых замечаний.

- Экспорт дублируется в `operators/export_operator.py` и `export_utils.py`; второй путь жёстко задаёт `rtx-remix/textures` в `TextureExporter.__init__`, первый учитывает настройку сцены. Нужен общий export plan и общий контракт, иначе исправления продолжат расходиться.
- Глобальные material caches используют USD path + хэш каталога и могут переживать изменения capture/mod. Нужно проверить invalidation при внешнем редактировании USD, смене файла в том же каталоге и повторном импорте; отдельное численное воспроизведение в этом аудите не выполнялось.
- Цепочки Math/ColorRamp/Mapping/UVMap/NormalMap Strength, несколько outputs, animated textures, UDIM и node groups не имеют полного экспортного контракта. Сначала зафиксировать поддержку и явный отказ, затем расширять.
- `copier.py` и relationship list ops: дополнительно проверить empty explicit/deleted targets, силу слоёв и циклические sublayers. Текущий набор тестов не является доказательством всех USD list-op случаев.
- Документация parity устарела: таблица octahedral по-прежнему описывает только encoding=0; счётчики README/badges — 153, фактически тестов 155. Отдельные Texture Management/parallel operators существуют в исходниках, но не подключены корневой регистрацией. Сверить реальные UI capabilities до заявлений о feature parity.
- В репозитории не обнаружен pipeline CI. Есть тестовый entry point, но нет автоматического барьера для регрессий. Текущие тесты часто проверяют присутствие атрибута/структуру без полного import→export→compose результата, каналов и повторного открытия .blend.
- Blender minimum указан как 4.0, тестирование проведено только на 4.5.5/5.1.2. Это граница проверки, а не доказанная несовместимость с 4.0. Совместимость с Linux/Wine также не проверялась.
- Оценить нагрузку на больших сценах: повторное создание XformCache, многократные обходы bpy.data.objects, загрузка полной PNG/RGBA-копии для больших нормалей, синхронный texconv до 120 секунд. Данных benchmark для утверждения конкретного ускорения нет.
- Сверить поставку LICENSE/NOTICE для заимствованных NVIDIA-модулей и texconv по исходным лицензиям. Заголовки NVIDIA в core имеются; юридическое заключение аудит не проводит.

## Что проверено без отдельного нового замечания

Memory-only DDS thumbnails не создают конвертированный файловый кэш; существующие tests thumbnail проходят. Низкоуровневые hide/unhide проверяют capture и сохраняют holder replacement; поведение игрового runtime требует отдельного запуска. `clean_mod_texture_suffixes.py` по умолчанию выполняет анализ, запрещает коллизии/бинарные USD/симлинки, создаёт ZIP backup с проверкой и пытается откатить ошибки записи; соответствующие tests проходят. Нельзя из этого делать вывод о ссылках из произвольных внешних модов: скрипт ограничен выбранным деревом.

## Рекомендуемый порядок работ

1. A01–A05: единая защита записи, staging, корректный результат операции и main-thread граница.
2. A06–A14: единый material/texture plan, повторный экспорт, каналы, нормали, persistence и конфликты.
3. A15–A20: общий geometry/instance/material bridge и применение мода.
4. A21–A23: единицы/оси света и камера.
5. A24–A28: cleanup, layers, lifecycle, rollback, hashes.
6. Документация, автоматические прогоны, оставшиеся проверки из раздела технического долга.

Для следующего этапа подготовлен `AUDIT_NEXT_WORK.ru.md`; диагностические данные исходного аудита сохранены в `audit-evidence.json`. Бинарник texconv не подвергался отдельному реверс-инжинирингу.
