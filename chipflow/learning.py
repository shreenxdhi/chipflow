"""One learning path shared by the browser and command line."""

LESSONS = [
    {
        "target": "sim",
        "title": "Test the behavior",
        "explanation": "RTL describes hardware. A testbench supplies inputs and checks the outputs before you spend time building a layout.",
        "output": "Pass message and waveform (.vcd)",
        "review": "Read the testbench and find the checks that produced PASS. Open the waveform to inspect reset, clock, and count.",
        "tools": "Icarus Verilog",
    },
    {
        "target": "synth",
        "title": "Turn RTL into gates",
        "explanation": "Synthesis maps the RTL to logic cells from a technology library.",
        "output": "Gate-level Verilog netlist (1_2_yosys.v)",
        "review": "Compare the gate-level netlist with counter.v. Look for registers and logic cells rather than behavioral statements.",
        "tools": "Yosys and OpenROAD-flow-scripts",
    },
    {
        "target": "floorplan",
        "title": "Plan the chip",
        "explanation": "Floorplanning sets the chip/core area and prepares pins, power, and places for large blocks.",
        "output": "Floorplan database (2_floorplan.odb)",
        "review": "Distinguish the die from the core. The example's small fixed dimensions are defaults for this counter, not every design.",
        "tools": "OpenROAD",
    },
    {
        "target": "place",
        "title": "Position the cells",
        "explanation": "Placement arranges logic cells inside the core while considering wire length and timing.",
        "output": "Placement database (3_place.odb)",
        "review": "Look at cell area and utilization. High density can make routing harder.",
        "tools": "OpenROAD",
    },
    {
        "target": "cts",
        "title": "Distribute the clock",
        "explanation": "Clock tree synthesis adds clock buffers so registers receive a controlled clock signal.",
        "output": "Clock-tree database (4_cts.odb)",
        "review": "Clock skew is the difference in clock arrival time. Setup and hold checks address different timing requirements.",
        "tools": "OpenROAD",
    },
    {
        "target": "route",
        "title": "Connect the cells",
        "explanation": "Routing connects cell pins with metal wires and vias, then checks routing rules.",
        "output": "Routed database (5_route.odb)",
        "review": "Router DRC counts are useful checks. They do not replace full foundry DRC or LVS signoff.",
        "tools": "OpenROAD",
    },
    {
        "target": "finish",
        "title": "Generate and review GDSII",
        "explanation": "Finishing writes the layout and final reports. GDSII stores the geometric shapes used to describe the chip.",
        "output": "Layout (6_final.gds), database, timing reports, and metrics",
        "review": "Open GDS in KLayout. Check reported setup/hold slack and router checks separately from successful tool completion. This example does not establish tapeout readiness.",
        "tools": "OpenROAD and KLayout",
    },
]
GLOSSARY = {
    "RTL": "Register-transfer level: code describing digital hardware and how registers update.",
    "Testbench": "Simulation code that drives inputs and checks a design's responses.",
    "Netlist": "A description of cells and the connections between them.",
    "Clock period": "Time between clock edges. A 10 ns period corresponds to 100 MHz.",
    "SDC": "Timing constraints: the clocks, I/O timing assumptions, and timing exceptions to analyze.",
    "PDK / platform": "Technology rules and cell-library views used to implement a design.",
    "Liberty / LEF": "Liberty describes cell timing/power; LEF describes physical abstracts used for layout.",
    "Setup / hold slack": "Timing margin for paths covered by the constraints; negative slack means a violation.",
    "DRC / LVS": "Design-rule checking checks geometry; layout-versus-schematic checks connectivity against a reference.",
    "GDSII": "A file format storing layout geometry. Generating it is one step before full signoff.",
}


def guide_text():
    lines = [
        "Your first RTL-to-GDS project",
        "",
        "Start a self-checking counter: chipflow quickstart first-chip",
        "Create an expert example: chipflow init custom-chip --expert",
        "",
    ]
    for index, lesson in enumerate(LESSONS, 1):
        lines.extend(
            [
                f"{index}. {lesson['title']} ({lesson['target']})",
                lesson["explanation"],
                "Output: " + lesson["output"],
                "Review: " + lesson["review"],
                "",
            ]
        )
    lines += [
        "Terms",
        "",
        *(f"{term}: {meaning}" for term, meaning in GLOSSARY.items()),
        "",
        "Physical targets repeat their prerequisites in independent runs. Keep the example small while learning.",
        "Own RTL: chipflow wizard chipflow.yaml",
        "Custom Tcl: chipflow hooks",
        "Flow settings: chipflow variables --search clock",
    ]
    return "\n".join(lines)
