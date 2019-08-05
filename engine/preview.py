from enum import Enum
from time import sleep
from mathutils import Matrix
from ..bin import pyluxcore
from .. import utils
from .. import export
from ..draw.final import FrameBufferFinal
from ..utils.log import LuxCoreLog

"""
Note: you can find the Blender preview scene in the sources at this path:
blender/release/datafiles/preview.blend
"""

# Diameter of the default sphere, in meters
DEFAULT_SPHERE_SIZE = 0.1

class PreviewType(Enum):
    NONE = 0
    MATERIAL = 1


# We use this as pyluxcore log handler to avoid spamming the console
def no_log_output(message):
    pass


def render(engine, depsgraph):
    scene = depsgraph.scene_eval    
    width, height = utils.calc_filmsize(scene)

    if max(width, height) <= 96:
        # We do not render thumbnails
        return

    pyluxcore.SetLogHandler(no_log_output)
    engine.exporter = export.Exporter()
    engine.exporter.scene = scene
    preview_type, active_mat = _get_preview_settings(engine.exporter, depsgraph)

    if preview_type == PreviewType.MATERIAL and not active_mat is None:
        engine.session = _export_mat_scene(engine, depsgraph, active_mat)
    else:
        print("Unsupported preview type")
        return enable_log_output()


    engine.framebuffer = FrameBufferFinal(scene)
    engine.session.Start()

    while True:
        try:
            engine.session.UpdateStats()
        except RuntimeError as error:
            print("Error during UpdateStats():", error)

        if engine.session.HasDone():
            break

        stats = engine.session.GetStats()
        samples = stats.Get("stats.renderengine.pass").GetInt()
        if (samples > 2 and samples < 10) or (samples > 0 and samples % 10 == 0):
            engine.framebuffer.draw(engine, engine.session, scene, False)
        sleep(1 / 30)

        if engine.test_break():
            # Abort as fast as possible, without drawing the framebuffer again
            engine.session.Stop()
            return enable_log_output()

    engine.framebuffer.draw(engine, engine.session, scene, True)
    engine.session.Stop()

    # Do not hold reference to temporary data
    engine.exporter.scene = None
    enable_log_output()


def enable_log_output():
    # Re-enable the log output
    pyluxcore.SetLogHandler(LuxCoreLog.add)


def _export_mat_scene(engine, depsgraph, active_mat):
    from ..export.caches.exported_data import ExportedObject
    from ..export.caches.exported_data import ExportedMesh
    from ..export.caches.object_cache import get_material
    from os import path

    exporter = engine.exporter
    scene = depsgraph.scene_eval

    # The diameter that the preview objects should have, in meters
    size = active_mat.luxcore.preview.size
    worldscale = size / DEFAULT_SPHERE_SIZE
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = worldscale

    scene_props = pyluxcore.Properties()
    luxcore_scene = pyluxcore.Scene()

    # The world sphere uses different lights and render settings
    is_world_sphere = active_mat.use_preview_world

    # Camera
    cam_props = export.camera.convert(exporter, scene)

    # Apply zoom
    field_of_view = cam_props.Get("scene.camera.fieldofview").GetFloat()
    cam_props.Set(pyluxcore.Property("scene.camera.autovolume.enable", 0))
    zoom = active_mat.luxcore.preview.zoom
    cam_props.Set(pyluxcore.Property("scene.camera.fieldofview", field_of_view / zoom))
    luxcore_scene.Parse(cam_props)

    # Objects
    for index, dg_obj_instance in enumerate(depsgraph.object_instances, start=1):
        obj = dg_obj_instance.instance_object if dg_obj_instance.is_instance else dg_obj_instance.object
        if not obj.name == 'preview_hair' and not exporter.object_cache2._is_visible(dg_obj_instance, obj):
            continue

        # Use LuxBall instead of Blender Shaderball
        if obj.name == "CurveCircle.002" or obj.name == "preview_shaderball.003":
            continue

        if obj.name == "preview_shaderball":
            use_instancing = False
            is_viewport_render = False
            obj_key = utils.make_key_from_instance(dg_obj_instance)
            mesh_key = exporter.object_cache2._get_mesh_key(obj, use_instancing, is_viewport_render)

            mesh_definitions = []
            props = pyluxcore.Properties()
            filepath = path.dirname(path.realpath(__file__))+"/../preview_scene/LuxCore_preview.ply"

            prefix = "scene.shapes." + mesh_key + "."
            props.Set(pyluxcore.Property(prefix + "type", "mesh"))
            props.Set(pyluxcore.Property(prefix + "ply", path.abspath(filepath)))
            mesh_definitions.append((mesh_key, 0))
            scene_props.Set(props)

            exported_mesh = ExportedMesh(mesh_definitions)

            if exported_mesh:
                mat_names = []
                for shape_name, mat_index in exported_mesh.mesh_definitions:
                    lux_mat_name, mat_props = get_material(obj, mat_index, exporter, depsgraph, is_viewport_render)
                    scene_props.Set(mat_props)
                    mat_names.append(lux_mat_name)

                exported_obj = ExportedObject(obj_key, exported_mesh.mesh_definitions, mat_names, None, True)

                scene_props.Set(exported_obj.get_props())
                exporter.object_cache2.exported_objects[obj_key] = exported_obj

        # Don't export lights and floor from preview scene
        elif not (obj.type == 'LIGHT' or obj.name == 'Floor'):
            exporter.object_cache2._convert_obj(exporter, dg_obj_instance, obj, depsgraph,
                                                luxcore_scene, scene_props, False)

    # Lights (either two area lights or a sun+sky setup)
    _create_lights(scene, luxcore_scene, scene_props, is_world_sphere)

##  #TODO: Decide if the ground plane should be visible with world sphere enabled
    if not is_world_sphere:
        _create_backplates(scene, luxcore_scene, scene_props)
    _create_ground(scene, luxcore_scene, scene_props)


    luxcore_scene.Parse(scene_props)

    # Session
    config_props = _create_config(scene, is_world_sphere)
    renderconfig = pyluxcore.RenderConfig(config_props, luxcore_scene)
    session = pyluxcore.RenderSession(renderconfig)
    
    return session

def _create_lights(scene, luxcore_scene, props, is_world_sphere):
    if is_world_sphere:
        props.Set(pyluxcore.Property("scene.lights.sky.type", "sky2"))
        props.Set(pyluxcore.Property("scene.lights.sky.gain", [.00003] * 3))
        # Building the visibility map and not needed in an open scene
        props.Set(pyluxcore.Property("scene.lights.sky.visibilitymap.enable", False))

        props.Set(pyluxcore.Property("scene.lights.sun.type", "sun"))
        props.Set(pyluxcore.Property("scene.lights.sun.dir", [-0.6, -1, 0.9]))
        props.Set(pyluxcore.Property("scene.lights.sun.gain", [.00003] * 3))
        # Avoid fireflies
        props.Set(pyluxcore.Property("scene.lights.sun.visibility.indirect.specular.enable", False))
    else:
        # Key light
        color_key = [80] * 3
        position_key = [4.5, -6, 5]
        rotation_key = Matrix(((0.523222804069519, 0.6480597257614136, 0.5534044504165649),
                (-0.22214478254318237, 0.7306543588638306, -0.6455973386764526),
                (-0.8227329850196838, 0.21485532820224762, 0.526258111000061)))
        scale_key = 1
        _create_area_light(scene, luxcore_scene, props, "key", color_key,
                           position_key, rotation_key, scale_key)

        # Fill light
        color_fill = [4] * 3
        position_fill = [-5.5, -2.5, 2.5]
        rotation_fill = Matrix(((0.19458825886249542, -0.4306204617023468, -0.8813066482543945),
                (0.13679763674736023, 0.9016143679618835, -0.4103388786315918, ),
                (0.9712990522384644, -0.04071354120969772, 0.23435142636299133 )))
        scale_fill = 2
        _create_area_light(scene, luxcore_scene, props, "fill", color_fill,
                           position_fill, rotation_fill, scale_fill, False)


def _create_area_light(scene, luxcore_scene, props, name, color, position, rotation_matrix, scale, visible=True):
    mat_name = name + "_mat"
    mesh_name = name + "_mesh"

    # Material
    props.Set(pyluxcore.Property("scene.materials." + mat_name + ".type", ["matte"]))
    props.Set(pyluxcore.Property("scene.materials." + mat_name + ".kd", [0.0] * 3))
    props.Set(pyluxcore.Property("scene.materials." + mat_name + ".emission", color))
    # assign material to object
    props.Set(pyluxcore.Property("scene.objects." + name + ".material", [mat_name]))
    props.Set(pyluxcore.Property("scene.objects." + name + ".camerainvisible", not visible))


    scale_matrix = Matrix()
    scale_matrix[0][0] = scale
    scale_matrix[1][1] = scale
    rotation_matrix.resize_4x4()
    transform_matrix = Matrix()
    transform_matrix[0][3] = position[0]
    transform_matrix[1][3] = position[1]
    transform_matrix[2][3] = position[2]

    mat = transform_matrix @ rotation_matrix @ scale_matrix
    transform = utils.matrix_to_list(mat, scene, apply_worldscale=True)

    # add mesh
    vertices = [
        (1, 1, 0),
        (1, -1, 0),
        (-1, -1, 0),
        (-1, 1, 0)
    ]
    faces = [
        (0, 1, 2),
        (2, 3, 0)
    ]
    luxcore_scene.DefineMesh(mesh_name, vertices, faces, None, None, None, None, transform)
    # assign mesh to object
    props.Set(pyluxcore.Property("scene.objects." + name + ".shape", [mesh_name]))
    return props


def _create_backplates(scene, luxcore_scene, props):
    worldscale = utils.get_worldscale(scene, as_scalematrix=False)

    # Ground plane
    size = 20*worldscale
    zpos = 0.0
    vertices = [
        (size, size, zpos),
        (size, -size, zpos),
        (-size, -size, zpos),
        (-size, size, zpos),
        (size, size, 0.5*size),
        (size, -size, 0.5*size),
        (-size, -size, 0.5*size),
        (-size, size, 0.5*size)
    ]
    faces = [
        (6, 5, 4),
        (4, 7, 6),
        (2, 3, 7),
        (7, 6, 2),
        (2, 6, 5),
        (5, 1, 2),
        (7, 3, 0),
        (0, 4, 7),
        (4, 0, 1),
        (1, 5, 4)
    ]
    _create_walls(luxcore_scene, props, "walls", vertices, faces, worldscale)

def _create_ground(scene, luxcore_scene, props):
    worldscale = utils.get_worldscale(scene, as_scalematrix=False)

    # Ground plane
    size = 20*worldscale
    zpos = 0.0
    vertices = [
        (size, size, zpos),
        (size, -size, zpos),
        (-size, -size, zpos),
        (-size, size, zpos)
    ]
    faces = [
        (0, 1, 2),
        (2, 3, 0),
    ]
    _create_checker_plane(luxcore_scene, props, "ground_plane", vertices, faces, worldscale)


def _create_checker_plane(luxcore_scene, props, name, vertices, faces, worldscale):
    mesh_name = name + "_mesh"
    mat_name = name + "_mat"
    tex_name = name + "_tex"

    # Mesh
    luxcore_scene.DefineMesh(mesh_name, vertices, faces, None, None, None, None)
    # Texture
    # (we scale the default sphere to be 10cm by default and we want the squares to be 5cm in size)
    checker_size = 5
    checker_trans = [checker_size, 0, 0, 0,
                     0, checker_size, 0, 0,
                     0, 0, checker_size, 0,
                     0, 0, 0, 1]
    props.Set(pyluxcore.Property("scene.textures." + tex_name + ".type", "checkerboard3d"))
    props.Set(pyluxcore.Property("scene.textures." + tex_name + ".texture1", 0.7))
    props.Set(pyluxcore.Property("scene.textures." + tex_name + ".texture2", 0.2))
    props.Set(pyluxcore.Property("scene.textures." + tex_name + ".mapping.type", "globalmapping3d"))
    props.Set(pyluxcore.Property("scene.textures." + tex_name + ".mapping.transformation", checker_trans))
    # Material
    props.Set(pyluxcore.Property("scene.materials." + mat_name + ".type", "matte"))
    props.Set(pyluxcore.Property("scene.materials." + mat_name + ".kd", tex_name))
    # Invisible for indirect diffuse rays to eliminate fireflies
    props.Set(pyluxcore.Property("scene.materials." + mat_name + ".visibility.indirect.diffuse.enable", False))

    # Object
    props.Set(pyluxcore.Property("scene.objects." + name + ".shape", mesh_name))
    props.Set(pyluxcore.Property("scene.objects." + name + ".material", mat_name))

def _create_walls(luxcore_scene, props, name, vertices, faces, worldscale):
    mesh_name = name + "_mesh"
    mat_name = name + "_mat"
    tex_name = name + "_tex"

    # Mesh
    luxcore_scene.DefineMesh(mesh_name, vertices, faces, None, None, None, None)
    # Texture
    # Material
    props.Set(pyluxcore.Property("scene.materials." + mat_name + ".type", "matte"))
    props.Set(pyluxcore.Property("scene.materials." + mat_name + ".kd", 0.7))
    # Invisible for indirect diffuse rays to eliminate fireflies
    props.Set(pyluxcore.Property("scene.materials." + mat_name + ".visibility.indirect.diffuse.enable", False))

    # Object
    props.Set(pyluxcore.Property("scene.objects." + name + ".shape", mesh_name))
    props.Set(pyluxcore.Property("scene.objects." + name + ".material", mat_name))


def _create_config(scene, is_world_sphere):
    prefix = ""

    width, height = utils.calc_filmsize(scene)

    if is_world_sphere:
        total_depth = 8
        diffuse_depth = 3
        specular_depth = 5
    else:
        total_depth = 8
        diffuse_depth = 3
        specular_depth = 5

    definitions = {
        "film.width": width,
        "film.height": height,

        "renderengine.type": "PATHCPU",
        "sampler.type": "SOBOL",
        # Focus as much as possible on the preview object
        "sampler.sobol.adaptive.strength": 0.95,

        "path.pathdepth.total": total_depth,
        "path.pathdepth.diffuse": diffuse_depth,
        "path.pathdepth.glossy": 5,
        "path.pathdepth.specular": specular_depth,

        "path.clamping.variance.maxvalue": 3,

        "film.filter.type": "BLACKMANHARRIS",
        "film.filter.width": 1.5,

        # The overhead of the kernel compilation is not worth it in our tiny preview
        "film.opencl.enable": False,
        # Imagepipeline
        "film.imagepipeline.0.type": "TONEMAP_LINEAR",
        "film.imagepipeline.0.scale": 1.0,

        # Preview quality
        "batch.halttime": 30,

        "batch.haltthreshold": 8 / 256,
        "batch.haltthreshold.warmup": 3,
        "batch.haltthreshold.step": 3,
        "batch.haltthreshold.filter.enable": False,
    }

    return utils.create_props(prefix, definitions)


def _convert_obj(exporter, obj, scene, luxcore_scene, props):
    obj_props, exported_obj = export.blender_object.convert(exporter, obj, scene, None, luxcore_scene, update_mesh=True)

    for psys in obj.particle_systems:
        settings = psys.settings
        if settings.type == "HAIR" and settings.render_type == "PATH":
            # Make the strands in strand preview mode thicker so they are visible
            settings.luxcore.hair.hair_size = 0.05
            settings.luxcore.hair.tesseltype = "solid"
            export.hair.convert_hair(exporter, obj, psys, luxcore_scene, scene)

    props.Set(obj_props)


def _get_preview_settings(exporter, depsgraph):    
    # Iterate through the preview scene, finding objects with materials attached
    objects = []
    for dg_obj_instance in depsgraph.object_instances:        
        obj = dg_obj_instance.instance_object if dg_obj_instance.is_instance else dg_obj_instance.object                
        
        if not obj.name == 'preview_hair' and not exporter.object_cache2._is_visible(dg_obj_instance, obj):
            continue        

        if obj.name.startswith("preview"):
            active_mat = obj.active_material
            objects.append(obj)

    if objects:
        return PreviewType.MATERIAL, active_mat

    return PreviewType.NONE, None
