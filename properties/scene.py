import bpy
from bpy.props import PointerProperty, IntProperty
from . import (
    config, debug, denoiser, denoiser_log, display, halt, lightgroups, opencl, statistics, viewport,
)


#def init():
#    bpy.types.Scene.luxcore = PointerProperty(type=LuxCoreScene)


class LuxCoreScene(bpy.types.PropertyGroup):
    config: PointerProperty(type=config.LuxCoreConfig)
    denoiser: PointerProperty(type=denoiser.LuxCoreDenoiser)
    denoiser_log: PointerProperty(type=denoiser_log.LuxCoreDenoiserLog)
    halt: PointerProperty(type=halt.LuxCoreHaltConditions)
    display: PointerProperty(type=display.LuxCoreDisplaySettings)
    opencl: PointerProperty(type=opencl.LuxCoreOpenCLSettings)
    lightgroups: PointerProperty(type=lightgroups.LuxCoreLightGroupSettings)
    viewport: PointerProperty(type=viewport.LuxCoreViewportSettings)
    statistics: PointerProperty(type=statistics.LuxCoreRenderStatsCollection)
    debug: PointerProperty(type=debug.LuxCoreDebugSettings)

    # Set during render and used during export
    # TODO does this have to be an IntProperty? why not active_layer_index = -1
    # TODO having it as property sometimes causes exceptions when in _RestrictContext
    active_layer_index: IntProperty(default=-1)

    @classmethod
    def register(cls):
        bpy.types.Scene.luxcore = PointerProperty(
            name="LuxCore Scene Settings",
            description="LuxCore scene settings",
            type=cls,
        )

    @classmethod
    def unregister(cls):
        del bpy.types.Scene.luxcore
