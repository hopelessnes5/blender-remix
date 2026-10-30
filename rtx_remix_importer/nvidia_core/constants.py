"""Toolkit constants used by the direct RTX Remix core.

Port of a subset of ``lightspeed.common.constants`` from NVIDIA RTX Remix
Toolkit 1.6.1 (Apache-2.0):

    SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
    SPDX-License-Identifier: Apache-2.0

Only the values needed by the capture/composition/prim code are copied.  The
original module imports ``omni.ui`` at import time, which is not available in
Blender, so the module cannot be reused by adding it to ``sys.path``.
"""

from __future__ import annotations

import re

# Layer metadata keys (lightspeed.layer_manager.core.data_models).
LAYER_TYPE_KEY = "lightspeed_layer_type"
LAYER_TYPE_CAPTURE = "capture"
LAYER_TYPE_REPLACEMENT = "replacement"
LAYER_TYPE_WORKFILE = "workfile"
LAYER_TYPE_CAPTURE_BAKER = "capture_baker"
LAYER_GAME_NAME_KEY = "lightspeed_game_name"
OMNI_LAYER_KEY = "omni_layer"
OMNI_LAYER_LOCKED_KEY = "locked"
OMNI_LAYER_MUTENESS_KEY = "muteness"

ROOTNODE = "/RootNode"
ROOTNODE_LOOKS = ROOTNODE + "/Looks"
ROOTNODE_INSTANCES = ROOTNODE + "/instances"
ROOTNODE_MESHES = ROOTNODE + "/meshes"
ROOTNODE_LIGHTS = ROOTNODE + "/lights"
CAPTURED_CAMERA = ROOTNODE + "/cameras/Camera"

CAPTURED_REMIX_SETTINGS = "/remix_settings"
CAPTURED_REMIX_CONFIG_ATTR = "remix:remix_config"
CAPTURED_REMIX_CONFIG_LEGACY_ATTR = "remix_config"

LIGHT_NAME_PREFIX = "light_"
INSTANCE_NAME_PREFIX = "inst_"
MESH_NAME_PREFIX = "mesh_"
MATERIAL_NAME_PREFIX = "mat_"

LIGHT_PATH = ROOTNODE_LIGHTS + "/" + LIGHT_NAME_PREFIX
INSTANCE_PATH = ROOTNODE_INSTANCES + "/" + INSTANCE_NAME_PREFIX
MESH_PATH = ROOTNODE_MESHES + "/" + MESH_NAME_PREFIX

IS_REMIX_REF_ATTR = "IsRemixRef"
MATERIAL_RELATIONSHIP = "material:binding"

USD_EXTENSIONS = (".usd", ".usda", ".usdc")

# Prim path regexes. Kept byte-for-byte equivalent to the Toolkit so the same
# paths classify the same way; ``REGEX_INSTANCE_TO_MESH_SUB`` is used by
# ``get_prototype``.
REGEX_IN_INSTANCE_PATH = (
    f"^(.*)({LIGHT_NAME_PREFIX}|{INSTANCE_NAME_PREFIX})([A-Z0-9]{{16}})(_[0-9]+)*/([a-zA-Z0-9_/]+)*$"
)
REGEX_MESH_PATH_BASE = rf"^(.*)({MESH_NAME_PREFIX})([A-Z0-9]{{16}})(_[0-9]+)*"
REGEX_MESH_PATH = rf"{REGEX_MESH_PATH_BASE}$"
REGEX_IN_MESH_PATH = rf"{REGEX_MESH_PATH_BASE}/([a-zA-Z0-9_/]+)*$"
REGEX_IN_MESH_CHILDREN_PATH = rf"{REGEX_MESH_PATH_BASE}/*([a-zA-Z0-9_/]+)*$"
REGEX_MESH_PATH_AND_CHILDREN = rf"{REGEX_MESH_PATH_BASE}(/*[a-zA-Z0-9_/]*)*$"
REGEX_INSTANCE_PATH = f"^(.*)({INSTANCE_NAME_PREFIX})([A-Z0-9]{{16}})(_[0-9]+)*$"
REGEX_LIGHT_PATH_BASE = f"^(.*)({LIGHT_NAME_PREFIX})([A-Z0-9]{{16}})(_[0-9]+)*"
REGEX_LIGHT_PATH = rf"{REGEX_LIGHT_PATH_BASE}$"
REGEX_LIGHT_PATH_AND_CHILDREN = rf"{REGEX_LIGHT_PATH_BASE}(/*[a-zA-Z0-9_/]*)*$"
REGEX_INSTANCE_TO_MESH_SUB = f"({LIGHT_PATH}|{INSTANCE_PATH}|{MESH_PATH})([A-Z0-9]{{16}})(_[0-9]+)"

COMPILED_REGEX_INSTANCE_TO_MESH_SUB = re.compile(REGEX_INSTANCE_TO_MESH_SUB)

# Particle systems are the one API-schema exception in the Toolkit's
# "transferable prim spec" logic (see ``prim_utils._prim_spec_has_definition_opinion``).
PARTICLE_SCHEMA_NAME = "ParticleSystemAPI"

# Blender-side metadata keys written on imported objects/meshes.
PROP_SOURCE_CAPTURE = "source_capture_path"
PROP_PROJECT_ID = "project_id"
PROP_USD_PRIM_PATH = "usd_prim_path"
PROP_USD_INSTANCE_PATH = "usd_instance_path"
PROP_USD_PROTOTYPE_PATH = "usd_prototype_path"
PROP_REMIX_MESH_HASH = "remix_mesh_hash"
PROP_SOURCE_LAYER = "source_layer"
