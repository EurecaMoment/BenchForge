"""Static mesh formats accepted by the portable asset importer."""

from pathlib import Path


MESH_SUFFIXES = frozenset({'.glb', '.gltf', '.obj', '.ply', '.stl'})


def validate_mesh_input(source):
    """Reject unsupported containers before scheduling or spawning conversion."""
    suffix = Path(source).suffix.lower()
    if suffix not in MESH_SUFFIXES:
        raise ValueError(
            'mesh_import accepts GLB, glTF, OBJ, PLY and STL static meshes. '
            'USD and robot assets must use benchforge_isaac_sim upload/native import '
            'and a SceneProgram native_asset handle; mesh conversion does not preserve articulation.')
