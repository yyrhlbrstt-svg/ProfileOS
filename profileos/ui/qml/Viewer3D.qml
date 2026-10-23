// The live 3D view of an opening.
//
// Built from the same section geometry that produces the cut list, lit like a
// product shot: an image-based studio light for the reflections that make
// anodised aluminium read as metal, a key light that casts real shadows, and
// a soft floor that catches them. Everything the page can change — finish,
// glass, wall, how far the leaves are open, the camera — is a property here,
// so the picture follows the form as it is typed.

import QtQuick
import QtQuick3D
import QtQuick3D.Helpers

Item {
    id: root

    // -- what is shown (set from Python) -----------------------------------
    property var frameGeometry: null
    property var glassGeometry: null
    property var panelGeometry: null
    property var gasketGeometry: null
    property var wallGeometry: null
    property var leaves: []
    property real radius: 3000
    property real modelHeight: 1500
    property real modelDepth: 80
    //: The parapet under the opening when it is shown in its wall.
    property real parapet: 900

    // -- how it is shown ----------------------------------------------------
    property color background: "#F5F5F7"
    property color floorColour: "#FFFFFF"
    //: A round fade, so the floor dissolves into the background with no edge.
    property url floorFade: ""
    property string finish: "natural"
    property bool showGlass: true
    property bool showWall: false
    property bool turntable: false
    property real openAmount: 0
    Behavior on openAmount { NumberAnimation { duration: 900; easing.type: Easing.InOutCubic } }

    readonly property var finishes: ({
        "natural":    { colour: "#C9CDD2", metal: 1.0,  rough: 0.30 },
        "silver":     { colour: "#D9DCDF", metal: 1.0,  rough: 0.18 },
        "bronze":     { colour: "#6B4C31", metal: 0.85, rough: 0.36 },
        "champagne":  { colour: "#BFA77F", metal: 0.9,  rough: 0.32 },
        "black":      { colour: "#1D1E20", metal: 0.0,  rough: 0.42 },
        "anthracite": { colour: "#383E42", metal: 0.15, rough: 0.45 },
        "white":      { colour: "#F0F0EC", metal: 0.0,  rough: 0.50 }
    })
    readonly property var look: finishes[finish] !== undefined ? finishes[finish] : finishes["natural"]

    // -- materials --------------------------------------------------------------
    PrincipledMaterial {
        id: aluminium
        baseColor: root.look.colour
        metalness: root.look.metal
        roughness: root.look.rough
        specularAmount: 0.6
        cullMode: Material.NoCulling
    }
    PrincipledMaterial {
        id: glass
        baseColor: "#D7ECEF"
        metalness: 0.0
        roughness: 0.03
        opacity: 0.22
        alphaMode: PrincipledMaterial.Blend
        specularAmount: 1.0
        cullMode: Material.NoCulling
        depthDrawMode: Material.NeverDepthDraw
    }
    PrincipledMaterial {
        id: panel
        baseColor: root.look.colour
        metalness: root.look.metal * 0.6
        roughness: 0.55
        cullMode: Material.NoCulling
    }
    PrincipledMaterial {
        id: gasket
        baseColor: "#1A1A1C"
        roughness: 0.8
        cullMode: Material.NoCulling
    }
    PrincipledMaterial {
        id: plaster
        baseColor: "#E4DFD7"
        roughness: 0.95
        cullMode: Material.NoCulling
    }

    View3D {
        id: view
        anchors.fill: parent
        camera: camera

        environment: ExtendedSceneEnvironment {
            backgroundMode: SceneEnvironment.Color
            clearColor: root.background
            antialiasingMode: SceneEnvironment.MSAA
            antialiasingQuality: SceneEnvironment.VeryHigh
            specularAAEnabled: true
            tonemapMode: SceneEnvironment.TonemapModeFilmic
            aoEnabled: true
            aoStrength: 35
            aoDistance: Math.max(20, root.modelDepth * 0.6)
            aoSoftness: 40
            lightProbe: Texture {
                textureData: ProceduralSkyTextureData {
                    skyTopColor: "#B8C4D0"
                    skyHorizonColor: "#F2F2F2"
                    groundHorizonColor: "#E6E6E6"
                    groundBottomColor: "#9A9A9A"
                    sunColor: "#FFFFFF"
                    sunLatitude: 35
                    sunLongitude: -40
                }
            }
            probeExposure: 0.75
            probeOrientation: Qt.vector3d(0, -30, 0)
        }

        // -- camera: orbit around the opening's centre ----------------------
        Node {
            id: orbit
            eulerRotation: Qt.vector3d(-10, -28, 0)
            PerspectiveCamera {
                id: camera
                z: root.radius
                fieldOfView: 32
                clipNear: 10
                clipFar: 500000
            }
        }

        // -- light ----------------------------------------------------------
        DirectionalLight {
            eulerRotation: Qt.vector3d(-42, -32, 0)
            brightness: 1.6
            castsShadow: true
            shadowMapQuality: Light.ShadowMapQualityVeryHigh
            shadowFactor: 78
            softShadowQuality: Light.PCF16
            pcfFactor: 6
            shadowMapFar: root.radius * 4
        }
        DirectionalLight {
            eulerRotation: Qt.vector3d(-18, 145, 0)
            brightness: 0.35
        }

        // -- the floor the shadow falls on --------------------------------
        Model {
            source: "#Rectangle"
            // The floor is the background's own colour, so there is no horizon
            // line — only the shadow says where the ground is, as in a studio.
            y: -root.modelHeight / 2 - (root.showWall ? root.parapet : 0) - 1
            eulerRotation.x: -90
            scale: Qt.vector3d(root.radius / 70, root.radius / 70, 1)
            receivesShadows: true
            castsShadows: false
            materials: PrincipledMaterial {
                baseColor: root.floorColour
                roughness: 1.0
                specularAmount: 0
                alphaMode: PrincipledMaterial.Blend
                opacityMap: Texture { source: root.floorFade }
                opacityChannel: Material.A
            }
        }

        // -- the fixed part: frame, mullions, fixed glass ----------------
        Model {
            geometry: root.frameGeometry ? root.frameGeometry : null
            visible: !!root.frameGeometry
            materials: [aluminium]
            castsShadows: true
            receivesShadows: true
        }
        Model {
            geometry: root.panelGeometry ? root.panelGeometry : null
            visible: !!root.panelGeometry
            materials: [panel]
        }
        Model {
            geometry: root.gasketGeometry ? root.gasketGeometry : null
            visible: !!root.gasketGeometry
            materials: [gasket]
        }
        Model {
            geometry: root.wallGeometry ? root.wallGeometry : null
            visible: root.showWall && !!root.wallGeometry
            materials: [plaster]
            castsShadows: true
            receivesShadows: true
        }
        Model {
            geometry: root.glassGeometry ? root.glassGeometry : null
            visible: root.showGlass && !!root.glassGeometry
            materials: [glass]
            castsShadows: false
        }

        // -- the leaves, each hung on its own hinge ----------------------
        Repeater3D {
            model: root.leaves
            delegate: Node {
                id: hinge
                required property var modelData
                readonly property string kind: modelData.kind
                readonly property real amount: root.openAmount
                position: Qt.vector3d(modelData.px, modelData.py, modelData.pz)
                eulerRotation.y: kind === "turn" ? modelData.sign * modelData.angle * amount
                               : kind === "pivot" ? modelData.angle * amount : 0
                eulerRotation.x: kind === "top" ? -modelData.angle * amount
                               : kind === "bottom" ? -modelData.angle * amount : 0

                Node {
                    position: Qt.vector3d(
                        -hinge.modelData.px + (hinge.kind === "slide" ? hinge.modelData.slide * hinge.amount : 0),
                        -hinge.modelData.py,
                        -hinge.modelData.pz)
                    Model {
                        geometry: hinge.modelData.alu ? hinge.modelData.alu : null
                        visible: !!hinge.modelData.alu
                        materials: [aluminium]
                        castsShadows: true
                        receivesShadows: true
                    }
                    Model {
                        geometry: hinge.modelData.glass ? hinge.modelData.glass : null
                        visible: root.showGlass && !!hinge.modelData.glass
                        materials: [glass]
                        castsShadows: false
                    }
                }
            }
        }
    }

    // -- mouse: drag to turn, wheel to zoom, right-drag to move ---------------
    OrbitCameraController {
        id: controller
        anchors.fill: parent
        origin: orbit
        camera: camera
        panEnabled: true
        xSpeed: 0.25
        ySpeed: 0.25
    }

    // A slow turn for showing a customer; stops the moment anyone drags.
    NumberAnimation {
        id: spin
        target: orbit
        property: "eulerRotation.y"
        from: orbit.eulerRotation.y
        to: orbit.eulerRotation.y - 360
        duration: 24000
        loops: Animation.Infinite
        running: root.turntable
    }

    // -- preset views ----------------------------------------------------------
    Vector3dAnimation { id: turnTo; target: orbit; property: "eulerRotation"; duration: 650; easing.type: Easing.InOutCubic }
    NumberAnimation { id: zoomTo; target: camera; property: "z"; duration: 650; easing.type: Easing.InOutCubic }
    Vector3dAnimation { id: centreTo; target: orbit; property: "position"; duration: 650; easing.type: Easing.InOutCubic }

    function setView(name) {
        root.turntable = false
        var angles = {
            "front":   Qt.vector3d(0, 0, 0),
            "angle":   Qt.vector3d(-10, -28, 0),
            "side":    Qt.vector3d(-5, -78, 0),
            "inside":  Qt.vector3d(-8, 152, 0),
            "above":   Qt.vector3d(-55, -20, 0),
            "detail":  Qt.vector3d(-14, -40, 0)
        }
        turnTo.stop(); zoomTo.stop(); centreTo.stop()
        turnTo.to = angles[name] !== undefined ? angles[name] : angles["angle"]
        zoomTo.to = name === "detail" ? root.radius * 0.38 : root.radius
        centreTo.to = name === "detail"
            ? Qt.vector3d(view.width > 0 ? root.radius * 0.0 : 0, -root.modelHeight * 0.32, 0)
            : Qt.vector3d(0, 0, 0)
        camera.x = 0; camera.y = 0
        turnTo.start(); zoomTo.start(); centreTo.start()
    }
}
