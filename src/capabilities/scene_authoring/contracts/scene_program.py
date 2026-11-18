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
Both versions use the configured Isaac Sim A100 a100sim.v1.Simulator service. The source schema accepts 512 objects and 64 cameras; all selected cameras are retained, split into native batches of at most 32. No local desktop simulator is required.
Keep scene metadata concise and preserve user requirements. Requested question counts belong to downstream question generation; semantic_layers do not substitute questions, collection or evidence.
Each object requires id,label,kind,size:[x,y,z] meters,xy:[x,y] WORLD position,support:"ground"|another_object_id,base_z:height offset above support,dynamic:boolean,mass_kg:number. Dynamic mass is at least .001; static mass may be zero. Optional yaw_deg defaults to zero. IDs use ASCII letters,digits,underscores and start with a letter or underscore; references preserve case.
support="ground" refers to world Z=0 and does not create a collider. Author floors and walls explicitly. Resolve support chains before positioning children; list order is irrelevant. IDs must exist and chains cannot be cyclic. Child bottom = support top + base_z. Negative floor heights and distant cameras are allowed without changing schema.
Portable capture kinds are box,sphere,cylinder (axis Z),container (five separate walls, open top),composite and registered mesh. Native catalog kinds need an explicit providers.IsaacSim4A100.asset_sources binding to an uploaded real asset. Do not substitute a proxy robot or box for a requested native model. Imported asset bounds may require additional native evidence.
For a real asset uploaded in this conversation, use kind="native" and native_asset:{asset_id,relative_path,size:[source_x,source_y,source_z],source_up_axis:"Y"|"Z",fix_base?:boolean}. Copy the asset_id and relative_path returned by benchforge_isaac_sim upload; never supply a host source path or change deployment configuration. native_asset.size is the source extent in meters before up-axis conversion; object size is the requested Z-up scene extent. Y-up source dimensions become [x,z,y] before scaling. This handle is checked against the conversation at admission and queued execution. Native import preserves the asset's topology/materials. Static validation does not certify articulation, locomotion, flight, contact or visual acceptance; inspect native capture and control receipts.
For generated assets supply generation with a detailed prompt and the existing asset-tool parameters. Asset generation must finish and register a mesh before capture. Mesh uses asset_id from the registered catalog. Textured meshes and Gaussian primary-surface annotations are not supported by the portable SceneProgram capture adapter; use the live native catalog for other render routes. Never silently replace a requested representation. A paired Gaussian asset must explicitly select render_representation="mesh" if mesh capture is intended.
Meshes preserve source proportions through uniform_fit. size is a bounding limit, not independent axis stretching. Optional mesh_transform:{orientation_deg_xyz:[x,y,z],scale_mode:"uniform_fit"} applies documented source-coordinate conversion, then X/Y/Z rotation, then uniform scaling; yaw_deg places it in the scene. Do not infer semantic pose by PCA. Actual oriented mesh bounds determine placement.
Object color:[r,g,b] in 0..1 is optional; named material base_color takes precedence. Portable appearance supports roughness and metallic constants. materials:[{id,name,base_color,roughness,metallic}] bind through material_id; object appearance overrides constants. Source mesh vertex colors remain available. Texture networks, texture_id, texture_tint, uv_scale_m, ior, specular and native_opacity_multiplier cannot be implicitly flattened into this renderer.
Composite parts require shape (box,sphere,cylinder),size,offset,color; optional rotation_deg_xyz,material_id,appearance. Parts inherit parent material constants unless a named part material or appearance overrides them. Rotate each part about its center in X/Y/Z order, then offset; object yaw is last. Dimensions are finite positive meters up to 1000 with no automatic minimum thickness. Declared composite size is the placement envelope; preview reports actual part bounds and support faces without clipping or resizing.
Optional physics:{static_friction,dynamic_friction,restitution,collider:"convexDecomposition",mass_source:"synthetic_prior"}. These are uncalibrated priors. Static geometry still requires collision and native bounds evidence. The native physics rollout and final transforms are separate from visual acceptance.
Cameras require position and target, differing by more than .0001m. Optional id,up,focal_length_mm,horizontal_aperture_mm; defaults are 24mm and 36mm. up must be nonzero and nonparallel to the view direction. Close-up and vertical views are allowed. Use target based on actual world placement, not local part offsets. Native matrices, rendered field of view and readout supply calibration. Cameras targeting a room are checked against its floor and ceiling; role:"external" or allow_external:true denotes an intentional exterior view.
Optional top-level fields are rooms,zones,portals,assets,materials,lights,navigation,affordances,interactions,semantic_layers,scene_graph,provenance,render_environment.
Room:{id,name,bounds:{center:[x,y,z],size:[x,y,z]},floor_z?,representation:"bounds_only"|"explicit_objects"|"imported_geometry",geometry?:{object_ids:[IDs],notes?},semantic_role?}. Zone:{id,name,bounds,room_id?,purpose?}. Bounds are semantic regions and create no geometry. Objects may reference room_id,zone_id,material_id and add semantic_labels,affordances,tags,metadata,source_ref; object affordances are string lists.
Portal:{id,kind,position,size,from_room?,to_room?,passable?}. Navigation fields: agents,waypoints,edges,clearance_m,source. They declare intent, not proven traversability.
Assets metadata:{id,kind,uri?,size?,provenance?:{source?,generator?,prompt_id?,license?,measured?}} does not import arbitrary paths. Scene graph:{nodes:[],edges:[{source,target,relation?}]}. Semantic layers:[{id,name,labels?,authority:"simulator"|"official"|"imported"|"generated"|"unknown"}]. Provenance fields:sources,generator,created_by,revision,notes. Generated labels are not official or measured.
The legacy SceneProgram actions apply_force,robot_push,record_trajectory have no implicit executable mapping in the A100 adapter. For real robot control, forces and synchronized trajectories, use benchforge_isaac_sim with the live catalog's physics.body,robot.command,robot.replay,world.observe and world.collect contracts. Keep original requested actions and surface any unsupported requirement; do not silently drop them or fabricate motion.
Portable render_environment supports renderer:"PathTracing",samples_per_pixel:16..256 and exposure:0. RTX RaytracedLighting and nonzero exposure must not be claimed or silently discarded.
Portable lights use id,kind:"environment"|"area",color,intensity,enabled. Area requires position and supports direction and size:[width,height] in meters. Environment is a constant dome. Intensity is passed as linear radiance, not calibrated lux; start with environment intensity 1 and inspect actual images. Point/spot/directional/sun, HDR environment_id, temperature and individual shadow overrides require another verified native route.
Read benchforge_scene_catalog for adapter limits and benchforge_isaac_sim action="catalog" for current server-native capability and availability. Parameters are synthetic priors. Native job acceptance is not completion, and actual captures do not by themselves certify realism or benchmark acceptance.'''
