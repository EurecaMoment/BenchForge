"""Serializable scene schemas, fields and authoring description."""

SCENE_V1_MAX_OBJECTS = SCENE_V2_MAX_OBJECTS = 512

SCENE_SCHEMAS = ('benchforge.scene/v1', 'benchforge.scene/v2')

SCENE_MAX_ROOMS = 64

SCENE_MAX_ZONES = 256

SCENE_MAX_PORTALS = 256

SCENE_MAX_ASSETS = 1024

LIGHT_KINDS = {'point', 'spot', 'area', 'directional', 'sun', 'environment'}

LIGHT_FIELDS = {'id', 'kind', 'position', 'direction', 'color', 'intensity', 'temperature', 'size', 'angle', 'inner_cone_deg', 'outer_cone_deg', 'cast_shadows', 'enabled', 'environment_id'}

RENDER_ENVIRONMENT_FIELDS = {'renderer', 'samples_per_pixel', 'exposure'}

RENDERERS = {'RaytracedLighting', 'PathTracing'}

SPATIALTREE_RECORDING_FIELDS = {'scenario_id', 'split'}

APPEARANCE_FIELDS={'roughness','metallic','specular','ior','texture_id','texture_tint','uv_scale_m','native_opacity_multiplier'}

SCENE_SCHEMA_DESCRIPTION = '''Return only a complete JSON SceneProgram with schema="benchforge.scene/v1" or "benchforge.scene/v2", scene_id (USD identifier), title, objects, cameras and assumptions (strings).
Both versions use the configured Isaac Sim isaacsim_rpc.v1.SimulationService. The source schema accepts 512 objects and 64 cameras; all selected cameras are retained and submitted in a single SimulateRequest each. No local desktop simulator is required.
Keep scene metadata concise and preserve user requirements. Requested question counts belong to downstream question generation; semantic_layers do not substitute questions, collection or evidence.
Each object requires id,label,kind,size:[x,y,z] meters,xy:[x,y] WORLD position,support:"ground"|another_object_id,base_z:height offset above support,dynamic:boolean. Optional mass_kg defaults: dynamic objects at least .001; static bodies may omit the field entirely (evidence records static_body_semantics='upstream_default'). Optional yaw_deg defaults to zero. IDs use ASCII letters,digits,underscores and start with a letter or underscore; references preserve case.
support="ground" refers to world Z=0 and does not create a collider. Author floors and walls explicitly. Resolve support chains before positioning children; list order is irrelevant. IDs must exist and chains cannot be cyclic. Child bottom = support top + base_z. Negative floor heights and distant cameras are allowed without changing schema.
Portable capture kinds are box, sphere (requires uniform size so radius maps faithfully), container (five separate box walls, open top), composite (parts limited to box + sphere; cylinder and other shapes are unsupported), and mesh (requires asset_id bound in providers.IsaacSim4A100.mesh_assets to a server directory containing mesh.npz). Cylinder (protocol has no BodySpec), native, generated, built-in native catalog assets and any other kind are rejected with an explicit UnsupportedScene before submission; do not silently substitute.
Meshes stage a pre-prepared mesh.npz on the service; mesh_assets binding maps the local conversation asset_id to an absolute server path. size in the scene is the requested Z-up extent; the service handles source proportions and orientation. Do not infer semantic pose by PCA; the oriented mesh bounds determine actual placement.
Object color:[r,g,b] in 0..1 is optional; named material base_color takes precedence. isaacsim_rpc.v1 BodySpec expresses only base color, so materials:[{id,name,base_color}] bind through material_id; roughness, metallic, specular, ior, texture_id, texture_tint, uv_scale_m and native_opacity_multiplier have no proto mapping and are rejected at admission rather than silently dropped.
Composite parts require shape (box or sphere only), size, offset; optional color, rotation_deg_xyz, material_id, appearance. Parts inherit parent material constants unless a named part material or appearance overrides them. Rotate each part about its center in X/Y/Z order, then offset; object yaw is last. Dimensions are finite positive meters up to 1000 with no automatic minimum thickness. Declared composite size is the placement envelope; preview reports actual part bounds and support faces without clipping or resizing.
Optional physics:{static_friction,dynamic_friction,restitution,collider:"convexDecomposition",mass_source:"synthetic_prior"}. These are uncalibrated priors accepted by BodySpec. Static geometry omits mass_kg entirely. The native physics rollout and final transforms are separate from visual acceptance.
Cameras require position and target, differing by more than .0001m. Optional id, up, focal_length_mm, horizontal_aperture_mm; defaults are 24mm and 36mm. FOV defaults to 50 degrees configured at the provider (evidence records fov_source='configured_default', not computed from focal_length_mm alone). up must be nonzero and nonparallel to the view direction. Close-up and vertical views are allowed. Use target based on actual world placement, not local part offsets. Cameras targeting a room are checked against its floor and ceiling; role:"external" or allow_external:true denotes an intentional exterior view. Azimuth, elevation and distance are derived from position/target after submission.
Optional top-level fields are rooms,zones,portals,assets,materials,lights,navigation,affordances,interactions,semantic_layers,scene_graph,provenance,render_environment.
Room:{id,name,bounds:{center:[x,y,z],size:[x,y,z]},floor_z?,representation:"bounds_only"|"explicit_objects"|"imported_geometry",geometry?:{object_ids:[IDs],notes?},semantic_role?}. Zone:{id,name,bounds,room_id?,purpose?}. Bounds are semantic regions and create no geometry. Objects may reference room_id,zone_id,material_id and add semantic_labels,affordances,tags,metadata,source_ref; object affordances are string lists.
Portal:{id,kind,position,size,from_room?,to_room?,passable?}. Navigation fields: agents,waypoints,edges,clearance_m,source. They declare intent, not proven traversability.
Assets metadata:{id,kind,uri?,size?,provenance?:{source?,generator?,prompt_id?,license?,measured?}} does not import arbitrary paths. Scene graph:{nodes:[],edges:[{source,target,relation?}]}. Semantic layers:[{id,name,labels?,authority:"simulator"|"official"|"imported"|"generated"|"unknown"}]. Provenance fields:sources,generator,created_by,revision,notes. Generated labels are not official or measured.
The legacy SceneProgram actions apply_force,robot_push,record_trajectory cannot be executed by isaacsim_rpc.v1 which only runs a forward simulation with frame capture; robot/contact/trajectory execution must be staged separately and is rejected at admission. Keep original requested actions as provenance; do not silently drop them or fabricate motion.
Portable render_environment accepts renderer:"PathTracing", samples_per_pixel:1..256 and exposure:0. isaacsim_rpc.v1 has no global exposure control; nonzero exposure is rejected. RTX RaytracedLighting is not supported by the v2 RenderSpec.
Portable lights use id, kind:"environment"|"area", color, intensity, enabled. Area requires position and supports direction and size:[width,height] in meters. Environment is a constant dome. Intensity is passed as linear radiance, not calibrated lux; start with environment intensity 1 and inspect actual images. Point/spot/directional/sun, HDR environment_id, temperature, cast_shadows and individual shadow overrides have no LightingSpec mapping and are rejected.
Admission validates static contract before any network call; UnsupportedScene collects every blocking issue (paths, object IDs, basis='isaacsim_rpc.v1') so the author can fix them together. Job acceptance is not completion; actual captures do not by themselves certify realism or benchmark acceptance.'''
