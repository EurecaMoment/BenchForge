window.BENCHFORGE_CASES = {
  "offline": [
    {
      "id": "sht",
      "source": "ALFRED",
      "embodiment": "Household robot",
      "capability": "Image-plane spatial relation: identify the utensil immediately left of an apple.",
      "image": "assets/images/sht.webp",
      "question": {
        "before": "The image shows a kitchen scene with a red apple near the front edge of the table. With left and right referring to positions in the image, which utensil is immediately to the left of the red apple?",
        "after": "The image shows a kitchen scene with a red apple near the front edge of the table. With left and right referring to positions in the image, which utensil is immediately to the left of the red apple?"
      },
      "options": {
        "before": ["A. Ladle", "B. Spatula", "C. Fork", "D. Knife"],
        "after": ["A. Ladle", "B. Spatula", "C. Fork", "D. Knife"]
      },
      "beforeAnswer": "B. Spatula",
      "afterAnswer": "A. Ladle",
      "evidenceBefore": "The RGB observation and its registered object record. Both identify a ladle, not a spatula.",
      "evidenceAfter": "The round bowl and long handle visible beside the apple agree with the registered Ladle.",
      "failure": "Answer derivability and evidence grounding: the initial answer contradicted both the visible evidence and the registered object record.",
      "repair": "Local Field Repair: correct only the Answer and exact-match scoring key.",
      "retained": ["Evidence", "Template", "Template-evidence binding"],
      "rebuilt": ["Answer and Scoring Protocol fields", "Requirement Contracts", "B1", "C1"],
      "outcome": "PASS"
    },
    {
      "id": "shn",
      "source": "Habitat",
      "embodiment": "Mobile robot",
      "capability": "Multi-view scene understanding: jointly check for an open window, the table beneath it, and part of the bed.",
      "image": "assets/images/shn.webp",
      "question": {
        "before": "The four numbered images show the same bedroom from different viewpoints. Which view shows the open window, the small table beneath it, and part of the bed at the same time?",
        "after": "The four numbered images show the same bedroom from different viewpoints. Which view shows the open window, the small table beneath it, and part of the bed at the same time?"
      },
      "options": {
        "before": ["A. View 1", "B. View 2—the only view satisfying all three conditions", "C. View 3", "D. View 4"],
        "after": ["A. View 1", "B. View 2", "C. View 3", "D. View 4"]
      },
      "beforeAnswer": "B. View 2",
      "afterAnswer": "B. View 2",
      "evidenceBefore": "Four registered RGB views from the same Habitat scene.",
      "evidenceAfter": "Only View 2 contains the open window, the table immediately beneath it, and the edge of the bed; every other view omits at least one condition.",
      "failure": "Target-signal dependence: the explanatory phrase attached only to option B revealed the answer without requiring inspection of the four views.",
      "repair": "Template Re-binding: remove the answer-revealing phrase so all options have the same form.",
      "retained": ["Scene collection", "View registration"],
      "rebuilt": ["Template-Evidence Binding", "Candidate Item Synthesis", "Requirement Contracts", "B1", "C1"],
      "outcome": "PASS"
    },
    {
      "id": "sa",
      "source": "LIBERO",
      "embodiment": "Robotic arm",
      "capability": "Temporal visual reasoning about the order of burner activation and object placement.",
      "image": "assets/images/sa.webp",
      "question": {
        "before": "Only the final frame of the robot demonstration is shown. Based on this image alone, which complete task did the robot most likely perform?",
        "after": "The four frames show the robot demonstration in chronological order. Which description best matches the observed order of burner activation and placement of the moka pot?"
      },
      "options": {
        "before": ["A. Turn on the stove and place the moka pot on it", "B. Put the black bowl on a plate", "C. Open a drawer and place a utensil inside", "D. Move the frying pan to the sink"],
        "after": ["A. The stove turns on before the moka pot is moved onto the lit burner", "B. The moka pot is placed first and the stove turns on afterward", "C. Both changes are already complete before Frame 1", "D. The stove turns on, but the moka pot never changes position"]
      },
      "beforeAnswer": "A",
      "afterAnswer": "A",
      "evidenceBefore": "A single terminal frame; the stated answer was derived from task metadata not provided to the evaluated model.",
      "evidenceAfter": "The burner is unlit in Frames 1 and 2 and red in Frame 3. The moka pot is then placed on the lit burner in Frame 4.",
      "failure": "Answer derivability and image dependence: a terminal frame cannot recover the complete task, so the initial answer depended on hidden task metadata.",
      "repair": "Candidate Re-synthesis: ask about the visible temporal relation and use the registered four-frame sequence.",
      "retained": ["Source demonstration", "Registered frame order"],
      "rebuilt": ["Candidate Item Synthesis", "Requirement Contracts", "B1", "C1"],
      "outcome": "PASS"
    },
    {
      "id": "sd",
      "source": "CARLA",
      "embodiment": "Autonomous vehicle",
      "capability": "Route choice relative to vehicle heading, grounded in aligned top-down and forward RGB views.",
      "image": "assets/images/sd.webp",
      "question": {
        "before": "The figure is an internal instance raster whose colors encode pipeline categories. If the vehicle must turn right at this intersection, which labeled exit should it take?",
        "after": "In the repaired media, the upper panel shows the intersection from above, and the lower panel shows the driver's forward view. The red arrow in the upper panel marks the vehicle's heading, and A, B, and C mark the available exits. If the vehicle turns right, which exit should it take?"
      },
      "options": {
        "before": ["A. Exit A", "B. Exit B", "C. Exit C", "D. Return along the approach road"],
        "after": ["A. Exit A", "B. Exit B", "C. Exit C", "D. Return along the approach road"]
      },
      "beforeAnswer": "C. Exit C",
      "afterAnswer": "C. Exit C",
      "evidenceBefore": "The internal instance raster and its paired pose and camera records.",
      "evidenceAfter": "The vehicle is aligned with the red arrow; from that heading, Exit C is the only branch on the vehicle's right.",
      "failure": "The model-visible media exposed an internal representation and omitted the visual anchors needed to interpret exits A, B, and C; the required processed-RGB media were absent.",
      "repair": "Skill Re-execution of the visual-anchor construction skill to produce an aligned top-down RGB view and a forward RGB view.",
      "retained": ["Other CARLA records"],
      "rebuilt": ["Visual-anchor construction", "Candidate Item Synthesis", "Requirement Contracts", "B1", "C1"],
      "outcome": "PASS"
    },
    {
      "id": "sq",
      "source": "TartanGround",
      "embodiment": "Quadruped robot",
      "capability": "Trajectory displacement and terminal camera heading from start and end observations.",
      "image": "assets/images/sq.webp",
      "question": {
        "before": "The side panels show the first and last camera views. The center panel plots the path from start S to end E; N indicates north, and the small arrows show the nearest cardinal camera heading at S and E. Where is E relative to S, and which direction is the camera facing at E?",
        "after": "The side panels show the first and last camera views. The center panel plots the path from start S to end E; N indicates north, and the small arrows show the nearest cardinal camera heading at S and E. Where is E relative to S, and which direction is the camera facing at E?"
      },
      "options": {
        "before": ["A. Southwest of S, facing west", "B. Southwest of S, facing north", "C. Southeast of S, facing west", "D. Northwest of S, facing east"],
        "after": ["A. Southwest of S, facing west", "B. Southwest of S, facing north", "C. Southeast of S, facing west", "D. Northwest of S, facing east"]
      },
      "beforeAnswer": "A. Southwest of S, facing west",
      "afterAnswer": "B. Southwest of S, facing north",
      "evidenceBefore": "Start and end images and the trajectory panel; the initial end arrow was incorrectly joined to intermediate pose 600.",
      "evidenceAfter": "The repaired red arrow uses pose 1,182, which matches the terminal image. E lies below and to the left of S, while the final arrow points north.",
      "failure": "Source traceability and answer derivability: the initial terminal image and terminal heading came from different indices, so changing the Answer alone could not restore evidence consistency.",
      "repair": "Upstream Rollback to Evidence and State Structuring; rebuild the evidence pool and affected descendants using the terminal pose.",
      "retained": ["The case does not explicitly name unchanged artifacts."],
      "rebuilt": ["Evidence and State Structuring", "Verified Evidence Pool", "Template-Evidence Binding", "Candidate Item Synthesis", "Affected gates"],
      "outcome": "PASS"
    },
    {
      "id": "su",
      "source": "Real aerial imagery",
      "embodiment": "Unmanned aerial vehicle (UAV)",
      "capability": "Relative-distance comparison between two marked vehicles in an aerial image.",
      "image": "assets/images/su.webp",
      "question": {
        "before": "The left panel shows an aerial photograph, and the right enlarges the region containing the two marked vehicles, A and B. Based only on these images, which vehicle is farther from the camera?",
        "after": null
      },
      "options": {
        "before": ["A. Vehicle A", "B. Vehicle B"],
        "after": null
      },
      "beforeAnswer": "B. Vehicle B",
      "afterAnswer": null,
      "evidenceBefore": "Hidden median-depth records are 29.73 m and 30.92 m, but detection confidences are only 0.230 and 0.226, both below the 0.25 evidence-admission threshold.",
      "evidenceAfter": "The marked targets are not resolved consistently enough in the source view and enlarged crop to support a reliable distance comparison. Hidden depth cannot substitute for admissible model-visible evidence.",
      "failure": "Evidence admissibility and human answerability: the visible media do not support a unique, reliable distance judgment, and neither target record reaches the admission threshold.",
      "repair": "Final Rejection: terminate the candidate lineage because changing the answer or wording cannot repair insufficient evidence.",
      "retained": ["Source image", "Unrelated verified UAV evidence"],
      "rebuilt": ["Final rejection only"],
      "outcome": "REJECTED"
    }
  ],
  "interactive": [
    {
      "taskId": "Kitchen27-00-slice-tomato",
      "title": "Slice and place a tomato",
      "split": "short_hard",
      "sceneType": "Kitchen",
      "subtasks": 1,
      "steps": 7,
      "invalidActions": 0,
      "success": true,
      "eqa": { "correct": 10, "total": 15, "display": "10/15" },
      "image": "assets/images/interactive-short.webp",
      "example": "examples/interactive-short.json",
      "instruction": "Pick up the tomato from the dining table, slice it, and put the slices on the counter top."
    },
    {
      "taskId": "Bathroom403-00-wash-stow",
      "title": "Wash and stow bathroom objects",
      "split": "long_hard",
      "sceneType": "Bathroom",
      "subtasks": 4,
      "steps": 60,
      "invalidActions": 4,
      "success": true,
      "eqa": { "correct": 9, "total": 15, "display": "9/15" },
      "image": "assets/images/interactive-long.webp",
      "example": "examples/interactive-long.json",
      "instruction": "Rinse the soap bar at the sink and put it on the counter top; rinse the cloth at the sink and put it on the counter top; put the scrub brush on the counter top; put the plunger on the counter top."
    }
  ]
};