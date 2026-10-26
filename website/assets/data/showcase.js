window.BENCHFORGE_SHOWCASE = {
  "selected_count": 33,
  "items_per_dataset": 10000,
  "total_dataset_items": 60000,
  "datasets": [
    {
      "dataset": "alfred",
      "suite": "SpatialHome-Task",
      "source": "ALFRED",
      "examples": [
        {
          "id": "ALFRED_GT_OBJECTIVE_003761",
          "capability_en": "Image-plane spatial relations",
          "question_type": "marked_object_choice",
          "question_type_en": "Marked-object choice",
          "difficulty": "medium",
          "question_original": "观察 A、B、C、D 的标记位置，哪一个最靠画面右边？",
          "question_en": "Looking at the marked positions A–D, which one lies farthest to the right in the image?",
          "choices_original": [
            "A. 标注物体 A",
            "B. 标注物体 B",
            "C. 标注物体 C",
            "D. 标注物体 D"
          ],
          "choices_en": [
            "A. Marked object A",
            "B. Marked object B",
            "C. Marked object C",
            "D. Marked object D"
          ],
          "answer_raw": "D",
          "answer_display_original": "D. 标注物体 D",
          "answer_display_en": "D. Marked object D",
          "images": [
            {
              "src": "assets/images/showcase/alfred/ALFRED_GT_OBJECTIVE_003761_1.webp",
              "label": "Marked observation",
              "width": 500,
              "height": 500,
              "source_relative_path": "assets/ALFRED_GT_OBJECTIVE_003761/ALFRED_GT_OBJECTIVE_003761_main.jpg"
            },
            {
              "src": "assets/images/showcase/alfred/ALFRED_GT_OBJECTIVE_003761_2.webp",
              "label": "Unmarked observation",
              "width": 500,
              "height": 500,
              "source_relative_path": "assets/ALFRED_GT_OBJECTIVE_003761/ALFRED_GT_OBJECTIVE_003761_aux_01.png"
            }
          ],
          "selection_note_en": "Compares four clearly marked points to test left-to-right localization in the image plane.",
          "provenance": {
            "dataset": "alfred",
            "record_id": "ALFRED_GT_OBJECTIVE_003761",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          },
          "capability_id": "C03_IMAGE_PLANE_SPATIAL",
          "capability": "图像平面空间关系"
        },
        {
          "id": "ALFRED_GT_OBJECTIVE_004018",
          "capability_en": "Counting and set size",
          "question_type": "interval_choice",
          "question_type_en": "Interval choice",
          "difficulty": "medium",
          "question_original": "画面中的生菜可见实例数落在哪个范围？",
          "question_en": "How many visible lettuce instances are in the image?",
          "choices_original": [
            "A. 0 个",
            "B. 1 个",
            "C. 2 个",
            "D. 3-4 个",
            "E. 5-8 个",
            "F. 9 个及以上"
          ],
          "choices_en": [
            "A. 0",
            "B. 1",
            "C. 2",
            "D. 3–4",
            "E. 5–8",
            "F. 9 or more"
          ],
          "answer_raw": "C",
          "answer_display_original": "C. 2 个",
          "answer_display_en": "C. 2",
          "images": [
            {
              "src": "assets/images/showcase/alfred/ALFRED_GT_OBJECTIVE_004018_1.webp",
              "label": "Rendered observation",
              "width": 300,
              "height": 300,
              "source_relative_path": "assets/ALFRED_GT_OBJECTIVE_004018/ALFRED_GT_OBJECTIVE_004018_main.png"
            }
          ],
          "selection_note_en": "Counts visible lettuce instances and maps the count to a discrete answer range.",
          "provenance": {
            "dataset": "alfred",
            "record_id": "ALFRED_GT_OBJECTIVE_004018",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          },
          "capability_id": "C02_COUNTING_SET_SIZE",
          "capability": "实例计数与集合大小"
        },
        {
          "id": "ALFRED_GT_OBJECTIVE_006098",
          "capability_en": "Receptacle and containment relations",
          "question_type": "multi_choice",
          "question_type_en": "Multiple choice",
          "difficulty": "hard",
          "question_original": "请多选当前放在 R 上或 R 里面的标注物体。",
          "question_en": "Select all marked objects that are currently on or inside R.",
          "choices_original": [
            "A. 标注物体 A",
            "B. 标注物体 B",
            "C. 标注物体 C",
            "D. 标注物体 D"
          ],
          "choices_en": [
            "A. Marked object A",
            "B. Marked object B",
            "C. Marked object C",
            "D. Marked object D"
          ],
          "answer_raw": "[\"A\",\"C\"]",
          "answer_display_original": "A. 标注物体 A; C. 标注物体 C",
          "answer_display_en": "A. Marked object A; C. Marked object C",
          "images": [
            {
              "src": "assets/images/showcase/alfred/ALFRED_GT_OBJECTIVE_006098_1.webp",
              "label": "Marked observation",
              "width": 500,
              "height": 500,
              "source_relative_path": "assets/ALFRED_GT_OBJECTIVE_006098/ALFRED_GT_OBJECTIVE_006098_main.jpg"
            },
            {
              "src": "assets/images/showcase/alfred/ALFRED_GT_OBJECTIVE_006098_2.webp",
              "label": "Unmarked observation",
              "width": 500,
              "height": 500,
              "source_relative_path": "assets/ALFRED_GT_OBJECTIVE_006098/ALFRED_GT_OBJECTIVE_006098_aux_01.png"
            }
          ],
          "selection_note_en": "Uses a marked support surface and four marked candidates to test on/inside relations.",
          "provenance": {
            "dataset": "alfred",
            "record_id": "ALFRED_GT_OBJECTIVE_006098",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          },
          "capability_id": "C05_RECEPTACLE_CONTAINMENT",
          "capability": "容器与承载关系"
        },
        {
          "id": "ALFRED_GT_OBJECTIVE_002001",
          "capability_en": "Temporal visibility and trajectories",
          "question_type": "multi_view_choice",
          "question_type_en": "Multi-view choice",
          "difficulty": "medium",
          "question_original": "比较图 A 和图 B，勺子是否只在其中一张图里可见？",
          "question_en": "Compare images A and B. Is the spoon visible in only one of them?",
          "choices_original": [
            "A. 是",
            "B. 否"
          ],
          "choices_en": [
            "A. Yes",
            "B. No"
          ],
          "answer_raw": "A",
          "answer_display_original": "A. 是",
          "answer_display_en": "A. Yes",
          "images": [
            {
              "src": "assets/images/showcase/alfred/ALFRED_GT_OBJECTIVE_002001_1.webp",
              "label": "A/B comparison",
              "width": 720,
              "height": 398,
              "source_relative_path": "assets/ALFRED_GT_OBJECTIVE_002001/ALFRED_GT_OBJECTIVE_002001_main.jpg"
            },
            {
              "src": "assets/images/showcase/alfred/ALFRED_GT_OBJECTIVE_002001_2.webp",
              "label": "Observation A",
              "width": 300,
              "height": 300,
              "source_relative_path": "assets/ALFRED_GT_OBJECTIVE_002001/ALFRED_GT_OBJECTIVE_002001_aux_01.png"
            },
            {
              "src": "assets/images/showcase/alfred/ALFRED_GT_OBJECTIVE_002001_3.webp",
              "label": "Observation B",
              "width": 300,
              "height": 300,
              "source_relative_path": "assets/ALFRED_GT_OBJECTIVE_002001/ALFRED_GT_OBJECTIVE_002001_aux_02.png"
            }
          ],
          "selection_note_en": "Requires comparing two observation views to identify a change in object visibility.",
          "provenance": {
            "dataset": "alfred",
            "record_id": "ALFRED_GT_OBJECTIVE_002001",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          },
          "capability_id": "C07_TEMPORAL_VISIBILITY_TRAJECTORY",
          "capability": "多帧可见性与轨迹顺序"
        },
        {
          "id": "ALFRED_GT_OBJECTIVE_002876",
          "capability_en": "Object visibility",
          "question_type": "multi_choice",
          "question_type_en": "Multiple choice",
          "difficulty": "medium",
          "question_original": "下面这些类别中，哪些确实出现在当前画面里？",
          "question_en": "Which of the following categories are actually present in the current view? Select all that apply.",
          "choices_original": [
            "A. 皮搋子",
            "B. 淋浴玻璃",
            "C. 毛巾架",
            "D. 盘子"
          ],
          "choices_en": [
            "A. Toilet plunger",
            "B. Shower glass",
            "C. Towel rack",
            "D. Plate"
          ],
          "answer_raw": "[\"B\",\"C\"]",
          "answer_display_original": "B. 淋浴玻璃; C. 毛巾架",
          "answer_display_en": "B. Shower glass; C. Towel rack",
          "images": [
            {
              "src": "assets/images/showcase/alfred/ALFRED_GT_OBJECTIVE_002876_1.webp",
              "label": "Rendered observation",
              "width": 300,
              "height": 300,
              "source_relative_path": "assets/ALFRED_GT_OBJECTIVE_002876/ALFRED_GT_OBJECTIVE_002876_main.png"
            }
          ],
          "selection_note_en": "Tests multi-select recognition of object categories in a rendered bathroom view.",
          "provenance": {
            "dataset": "alfred",
            "record_id": "ALFRED_GT_OBJECTIVE_002876",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          },
          "capability_id": "C01_OBJECT_VISIBILITY",
          "capability": "可见对象与类别识别"
        },
        {
          "id": "ALFRED_GT_OBJECTIVE_002365",
          "capability_en": "Object state and affordance",
          "question_type": "multi_choice",
          "question_type_en": "Multiple choice",
          "difficulty": "medium",
          "question_original": "A、B、C、D 中，请选出所有当前支持被切开的物体。",
          "question_en": "Which of objects A–D can currently be sliced? Select all that apply.",
          "choices_original": [
            "A. 标注物体 A",
            "B. 标注物体 B",
            "C. 标注物体 C",
            "D. 标注物体 D"
          ],
          "choices_en": [
            "A. Marked object A",
            "B. Marked object B",
            "C. Marked object C",
            "D. Marked object D"
          ],
          "answer_raw": "[\"B\"]",
          "answer_display_original": "B. 标注物体 B",
          "answer_display_en": "B. Marked object B",
          "images": [
            {
              "src": "assets/images/showcase/alfred/ALFRED_GT_OBJECTIVE_002365_1.webp",
              "label": "Marked observation",
              "width": 500,
              "height": 500,
              "source_relative_path": "assets/ALFRED_GT_OBJECTIVE_002365/ALFRED_GT_OBJECTIVE_002365_main.jpg"
            },
            {
              "src": "assets/images/showcase/alfred/ALFRED_GT_OBJECTIVE_002365_2.webp",
              "label": "Unmarked observation",
              "width": 500,
              "height": 500,
              "source_relative_path": "assets/ALFRED_GT_OBJECTIVE_002365/ALFRED_GT_OBJECTIVE_002365_aux_01.png"
            }
          ],
          "selection_note_en": "Tests identifying the marked candidate whose visible object type supports slicing.",
          "provenance": {
            "dataset": "alfred",
            "record_id": "ALFRED_GT_OBJECTIVE_002365",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          },
          "capability_id": "C08_OBJECT_STATE_AFFORDANCE",
          "capability": "对象状态与可操作属性"
        }
      ]
    },
    {
      "dataset": "habitat",
      "suite": "SpatialHome-Nav",
      "source": "Habitat",
      "examples": [
        {
          "id": "apartment_1_HAB_C05_Q01_LARGEST_STEP_0000",
          "capability_en": "Trajectory segment comparison",
          "question_type": "multi_view_choice",
          "question_type_en": "Multi-view choice",
          "difficulty": "hard",
          "question_original": "比较 T1→T2、T2→T3、T3→T4，哪一步在俯视图上最长？",
          "question_en": "Compare T1→T2, T2→T3, and T3→T4. Which segment is longest in the top-down view?",
          "choices_original": [
            "A. T3→T4",
            "B. T1→T2",
            "C. T2→T3"
          ],
          "choices_en": [
            "A. T3→T4",
            "B. T1→T2",
            "C. T2→T3"
          ],
          "answer_raw": "C",
          "answer_display_original": "C. T2→T3",
          "answer_display_en": "C. T2→T3",
          "images": [
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q01_LARGEST_STEP_0000_1.webp",
              "label": "Trajectory and observation overlay",
              "width": 918,
              "height": 446,
              "source_relative_path": "assets/overlays/apartment_1_HAB_C05_Q01_LARGEST_STEP_0000.jpg"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q01_LARGEST_STEP_0000_2.webp",
              "label": "Observation frame T1",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00081.png"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q01_LARGEST_STEP_0000_3.webp",
              "label": "Observation frame T2",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00082.png"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q01_LARGEST_STEP_0000_4.webp",
              "label": "Observation frame T3",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00083.png"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q01_LARGEST_STEP_0000_5.webp",
              "label": "Observation frame T4",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00084.png"
            }
          ],
          "selection_note_en": "Compares successive camera-motion segments against a labeled top-down path; the winning segment is visibly separated in length.",
          "provenance": {
            "dataset": "habitat",
            "record_id": "apartment_1_HAB_C05_Q01_LARGEST_STEP_0000",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          },
          "capability_id": "HAB_C05",
          "capability": "相机平面轨迹"
        },
        {
          "id": "apartment_1_HAB_C08_Q03_DISTANCE_FROM_START_ORDER_0036",
          "capability_en": "Trajectory distance ordering",
          "question_type": "ordered_list",
          "question_type_en": "Ordered list",
          "difficulty": "hard",
          "question_original": "以后续采集点到 T1 的平面距离为准，给出由近到远的顺序。",
          "question_en": "Using planar distance from T1 to the later capture points, order them from nearest to farthest.",
          "choices_original": [
            "A. 时刻 T2",
            "B. 时刻 T4",
            "C. 时刻 T3"
          ],
          "choices_en": [
            "A. Time step T2",
            "B. Time step T4",
            "C. Time step T3"
          ],
          "answer_raw": "[\"A\",\"C\",\"B\"]",
          "answer_display_original": "A. 时刻 T2 → C. 时刻 T3 → B. 时刻 T4",
          "answer_display_en": "A. Time step T2 → C. Time step T3 → B. Time step T4",
          "images": [
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C08_Q03_DISTANCE_FROM_START_ORDER_0036_1.webp",
              "label": "Trajectory and observation overlay",
              "width": 918,
              "height": 446,
              "source_relative_path": "assets/overlays/apartment_1_HAB_C08_Q03_DISTANCE_FROM_START_ORDER_0036.jpg"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C08_Q03_DISTANCE_FROM_START_ORDER_0036_2.webp",
              "label": "Observation frame T1",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00124.png"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C08_Q03_DISTANCE_FROM_START_ORDER_0036_3.webp",
              "label": "Observation frame T2",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00125.png"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C08_Q03_DISTANCE_FROM_START_ORDER_0036_4.webp",
              "label": "Observation frame T3",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00126.png"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C08_Q03_DISTANCE_FROM_START_ORDER_0036_5.webp",
              "label": "Observation frame T4",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00127.png"
            }
          ],
          "selection_note_en": "Ranks later camera positions from a labeled, north-oriented trajectory with distinct distances from T1.",
          "provenance": {
            "dataset": "habitat",
            "record_id": "apartment_1_HAB_C08_Q03_DISTANCE_FROM_START_ORDER_0036",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          },
          "capability_id": "HAB_C08",
          "capability": "采集顺序与多视角一致性"
        },
        {
          "id": "apartment_1_HAB_C05_Q02_NET_DIRECTION_0005",
          "capability_en": "Net camera displacement",
          "question_type": "single_choice",
          "question_type_en": "Single choice",
          "difficulty": "medium",
          "question_original": "从起点 T1 到终点 T4，总体位移更接近哪个方向？",
          "question_en": "From T1 to T4, in which direction is the net displacement primarily oriented?",
          "choices_original": [
            "A. 朝北",
            "B. 朝东",
            "C. 朝西",
            "D. 朝南"
          ],
          "choices_en": [
            "A. North",
            "B. East",
            "C. West",
            "D. South"
          ],
          "answer_raw": "A",
          "answer_display_original": "A. 朝北",
          "answer_display_en": "A. North",
          "images": [
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q02_NET_DIRECTION_0005_1.webp",
              "label": "Trajectory and observation overlay",
              "width": 918,
              "height": 446,
              "source_relative_path": "assets/overlays/apartment_1_HAB_C05_Q02_NET_DIRECTION_0005.jpg"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q02_NET_DIRECTION_0005_2.webp",
              "label": "Observation frame T1",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00209.png"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q02_NET_DIRECTION_0005_3.webp",
              "label": "Observation frame T2",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00210.png"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q02_NET_DIRECTION_0005_4.webp",
              "label": "Observation frame T3",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00211.png"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q02_NET_DIRECTION_0005_5.webp",
              "label": "Observation frame T4",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00212.png"
            }
          ],
          "selection_note_en": "Reads the start-to-end displacement from the top-down path and its north arrow.",
          "provenance": {
            "dataset": "habitat",
            "record_id": "apartment_1_HAB_C05_Q02_NET_DIRECTION_0005",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          },
          "capability_id": "HAB_C05",
          "capability": "相机平面轨迹"
        },
        {
          "id": "apartment_1_HAB_C06_Q01_HEADING_CHANGE_0003",
          "capability_en": "Camera heading change",
          "question_type": "multi_view_choice",
          "question_type_en": "Multi-view choice",
          "difficulty": "medium",
          "question_original": "只看俯视图里的箭头，选择最合适的转向描述。",
          "question_en": "Based only on the arrows in the top-down view, choose the most suitable description of the turn.",
          "choices_original": [
            "A. 俯视图中顺时针转动",
            "B. 接近反向转动",
            "C. 俯视图中逆时针转动",
            "D. 基本保持原方向"
          ],
          "choices_en": [
            "A. Clockwise turn in the top-down view",
            "B. Almost a reversal of direction",
            "C. Counterclockwise turn in the top-down view",
            "D. Orientation remains nearly unchanged"
          ],
          "answer_raw": "B",
          "answer_display_original": "B. 接近反向转动",
          "answer_display_en": "B. Almost a reversal of direction",
          "images": [
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C06_Q01_HEADING_CHANGE_0003_1.webp",
              "label": "Trajectory and observation overlay",
              "width": 918,
              "height": 229,
              "source_relative_path": "assets/overlays/apartment_1_HAB_C06_Q01_HEADING_CHANGE_0003.jpg"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C06_Q01_HEADING_CHANGE_0003_2.webp",
              "label": "Observation frame T1",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00227.png"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C06_Q01_HEADING_CHANGE_0003_3.webp",
              "label": "Observation frame T2",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00228.png"
            }
          ],
          "selection_note_en": "Compares two explicitly drawn camera-heading arrows at T1 and T2.",
          "provenance": {
            "dataset": "habitat",
            "record_id": "apartment_1_HAB_C06_Q01_HEADING_CHANGE_0003",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          },
          "capability_id": "HAB_C06",
          "capability": "相机朝向变化"
        },
        {
          "id": "apartment_1_HAB_C05_Q03_CLOSEST_REVISIT_0012",
          "capability_en": "Trajectory return proximity",
          "question_type": "multi_view_choice",
          "question_type_en": "Multi-view choice",
          "difficulty": "hard",
          "question_original": "后面哪个采集点最像回到 T1 附近？",
          "question_en": "Which later capture point comes closest to T1?",
          "choices_original": [
            "A. 时刻 T4",
            "B. 时刻 T3",
            "C. 时刻 T2"
          ],
          "choices_en": [
            "A. Time step T4",
            "B. Time step T3",
            "C. Time step T2"
          ],
          "answer_raw": "C",
          "answer_display_original": "C. 时刻 T2",
          "answer_display_en": "C. Time step T2",
          "images": [
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q03_CLOSEST_REVISIT_0012_1.webp",
              "label": "Trajectory and observation overlay",
              "width": 918,
              "height": 446,
              "source_relative_path": "assets/overlays/apartment_1_HAB_C05_Q03_CLOSEST_REVISIT_0012.jpg"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q03_CLOSEST_REVISIT_0012_2.webp",
              "label": "Observation frame T1",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00345.png"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q03_CLOSEST_REVISIT_0012_3.webp",
              "label": "Observation frame T2",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00346.png"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q03_CLOSEST_REVISIT_0012_4.webp",
              "label": "Observation frame T3",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00347.png"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C05_Q03_CLOSEST_REVISIT_0012_5.webp",
              "label": "Observation frame T4",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00348.png"
            }
          ],
          "selection_note_en": "Finds the later camera position nearest T1 using the same labeled top-down path.",
          "provenance": {
            "dataset": "habitat",
            "record_id": "apartment_1_HAB_C05_Q03_CLOSEST_REVISIT_0012",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          },
          "capability_id": "HAB_C05",
          "capability": "相机平面轨迹"
        },
        {
          "id": "apartment_1_HAB_C06_Q02_CARDINAL_HEADING_0011",
          "capability_en": "Cardinal camera heading",
          "question_type": "single_choice",
          "question_type_en": "Single choice",
          "difficulty": "simple",
          "question_original": "根据右侧俯视图，T1 的相机主要朝哪个方向？",
          "question_en": "Which cardinal direction is the camera at T1 primarily facing?",
          "choices_original": [
            "A. 朝东",
            "B. 朝南",
            "C. 朝北",
            "D. 朝西"
          ],
          "choices_en": [
            "A. East",
            "B. South",
            "C. North",
            "D. West"
          ],
          "answer_raw": "B",
          "answer_display_original": "B. 朝南",
          "answer_display_en": "B. South",
          "images": [
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C06_Q02_CARDINAL_HEADING_0011_1.webp",
              "label": "Trajectory and observation overlay",
              "width": 686,
              "height": 229,
              "source_relative_path": "assets/overlays/apartment_1_HAB_C06_Q02_CARDINAL_HEADING_0011.jpg"
            },
            {
              "src": "assets/images/showcase/habitat/apartment_1_HAB_C06_Q02_CARDINAL_HEADING_0011_2.webp",
              "label": "Observation frame T1",
              "width": 640,
              "height": 480,
              "source_relative_path": "assets/base_images/apartment_1_00142.png"
            }
          ],
          "selection_note_en": "Reads the T1 heading arrow against the north indicator shown in the trajectory panel.",
          "provenance": {
            "dataset": "habitat",
            "record_id": "apartment_1_HAB_C06_Q02_CARDINAL_HEADING_0011",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          },
          "capability_id": "HAB_C06",
          "capability": "相机朝向变化"
        }
      ]
    },
    {
      "dataset": "carla",
      "suite": "SpatialDrive",
      "source": "CARLA",
      "examples": [
        {
          "id": "Town10HD_Opt_front_00003_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_003_005763",
          "capability": "图像平面空间关系",
          "capability_en": "Image-plane spatial relation",
          "capability_id": "O3_image_plane_spatial_relation",
          "question_type": "ordered_list",
          "question_type_en": "Ordered list",
          "difficulty": "medium",
          "question_original": "观察标注图，A、B、C 在画面里的横向顺序是什么？",
          "question_en": "In the annotated image, what is the left-to-right order of A, B, and C?",
          "choices_original": [
            "A. 标注物体 A",
            "B. 标注物体 B",
            "C. 标注物体 C"
          ],
          "choices_en": [
            "A. Marked object A",
            "B. Marked object B",
            "C. Marked object C"
          ],
          "answer_raw": "[\"B\",\"A\",\"C\"]",
          "answer_display_original": "从左到右（按标注框中心）：标注对象 B → 标注对象 A → 标注对象 C",
          "answer_display_en": "Left to right by marked-box center: object B → object A → object C",
          "images": [
            {
              "src": "assets/images/showcase/carla/Town10HD_Opt_front_00003_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_003_005763.webp",
              "label": "Annotated RGB observation",
              "width": 640,
              "height": 360,
              "source_relative_path": "assets/Town10HD_Opt_front_00003_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_003_005763/Town10HD_Opt_front_00003_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_003_005763_overlay.jpg"
            }
          ],
          "selection_note_en": "Orders the centers of the visible marked boxes from left to right.",
          "provenance": {
            "dataset": "carla",
            "record_id": "Town10HD_Opt_front_00003_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_003_005763",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "Town10HD_Opt_side_right_00033_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_004_002756",
          "capability": "图像平面空间关系",
          "capability_en": "Image-plane spatial relation",
          "capability_id": "O3_image_plane_spatial_relation",
          "question_type": "ordered_list",
          "question_type_en": "Ordered list",
          "difficulty": "medium",
          "question_original": "将这些候选标注目标按从左到右排列。",
          "question_en": "Order the marked targets from left to right.",
          "choices_original": [
            "A. 标注物体 A",
            "B. 标注物体 B",
            "C. 标注物体 C"
          ],
          "choices_en": [
            "A. Marked object A",
            "B. Marked object B",
            "C. Marked object C"
          ],
          "answer_raw": "[\"B\",\"A\",\"C\"]",
          "answer_display_original": "从左到右（按标注框中心）：标注对象 B → 标注对象 A → 标注对象 C",
          "answer_display_en": "Left to right by marked-box center: object B → object A → object C",
          "images": [
            {
              "src": "assets/images/showcase/carla/Town10HD_Opt_side_right_00033_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_004_002756.webp",
              "label": "Annotated RGB observation",
              "width": 640,
              "height": 360,
              "source_relative_path": "assets/Town10HD_Opt_side_right_00033_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_004_002756/Town10HD_Opt_side_right_00033_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_004_002756_overlay.jpg"
            }
          ],
          "selection_note_en": "Orders the centers of the visible marked boxes from left to right.",
          "provenance": {
            "dataset": "carla",
            "record_id": "Town10HD_Opt_side_right_00033_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_004_002756",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "Town01_Opt_side_right_00029_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_001_000061",
          "capability": "图像平面空间关系",
          "capability_en": "Image-plane spatial relation",
          "capability_id": "O3_image_plane_spatial_relation",
          "question_type": "ordered_list",
          "question_type_en": "Ordered list",
          "difficulty": "medium",
          "question_original": "观察标注图，A、B、C 在画面里的横向顺序是什么？",
          "question_en": "In the annotated image, what is the left-to-right order of A, B, and C?",
          "choices_original": [
            "A. 标注物体 A",
            "B. 标注物体 B",
            "C. 标注物体 C"
          ],
          "choices_en": [
            "A. Marked object A",
            "B. Marked object B",
            "C. Marked object C"
          ],
          "answer_raw": "[\"B\",\"C\",\"A\"]",
          "answer_display_original": "从左到右（按标注框中心）：标注对象 B → 标注对象 C → 标注对象 A",
          "answer_display_en": "Left to right by marked-box center: object B → object C → object A",
          "images": [
            {
              "src": "assets/images/showcase/carla/Town01_Opt_side_right_00029_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_001_000061.webp",
              "label": "Annotated RGB observation",
              "width": 640,
              "height": 360,
              "source_relative_path": "assets/Town01_Opt_side_right_00029_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_001_000061/Town01_Opt_side_right_00029_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_001_000061_overlay.jpg"
            }
          ],
          "selection_note_en": "Orders the centers of the visible marked boxes from left to right.",
          "provenance": {
            "dataset": "carla",
            "record_id": "Town01_Opt_side_right_00029_CARLA_GT_O3_IMAGE_PLANE_SPATIAL_001_000061",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        }
      ]
    },
    {
      "dataset": "libero",
      "suite": "SpatialArm",
      "source": "LIBERO",
      "examples": [
        {
          "id": "LIBERO-OBJ-000048",
          "capability": "双视角一致性与相机识别",
          "capability_id": "LC9_MULTI_VIEW_CONSISTENCY",
          "capability_en": "Multi-view consistency",
          "question_type": "multi_view_choice",
          "question_type_en": "Multi-view choice",
          "difficulty": "simple",
          "question_original": "从两个相机画面中选出手眼相机视角。",
          "question_en": "Select the eye-in-hand camera view from the two camera images.",
          "choices_original": [
            "A. 面板 A",
            "B. 面板 B"
          ],
          "choices_en": [
            "A. Panel A",
            "B. Panel B"
          ],
          "answer_raw": "B",
          "answer_display_original": "B. 面板 B",
          "answer_display_en": "B. Panel B",
          "images": [
            {
              "src": "assets/images/showcase/libero/01-libero-obj-000048.webp",
              "label": "Camera panels A and B",
              "width": 548,
              "height": 310,
              "source_relative_path": "assets/libero_goal_open_the_middle_drawer_of_the_cabinet_seed3_step0001_003225/LIBERO-OBJ-000048_overlay.png"
            }
          ],
          "selection_note_en": "The two synchronized camera panels make the wrist-camera view distinguishable from the scene camera.",
          "provenance": {
            "dataset": "libero",
            "record_id": "LIBERO-OBJ-000048",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "LIBERO-OBJ-000030",
          "capability": "时序轨迹与状态变化",
          "capability_id": "LC7_TEMPORAL_TRAJECTORY",
          "capability_en": "Temporal trajectory",
          "question_type": "temporal_choice",
          "question_type_en": "Temporal choice",
          "difficulty": "medium",
          "question_original": "观察轨迹标识，末端从开始到结束的主要方向是什么？",
          "question_en": "From the trajectory visualization, what is the end effector’s primary direction of motion from start to end?",
          "choices_original": [
            "A. 向下",
            "B. 向后",
            "C. 向右",
            "D. 向左",
            "E. 向上",
            "F. 动作幅度很小",
            "G. 向前"
          ],
          "choices_en": [
            "A. Down",
            "B. Backward",
            "C. To the right",
            "D. To the left",
            "E. Up",
            "F. Movement is minimal",
            "G. Forward"
          ],
          "answer_raw": "C",
          "answer_display_original": "C. 向右",
          "answer_display_en": "C. To the right",
          "images": [
            {
              "src": "assets/images/showcase/libero/02-libero-obj-000030.webp",
              "label": "Start, goal, and end-effector path",
              "width": 868,
              "height": 274,
              "source_relative_path": "assets/libero_90_KITCHEN_SCENE4_put_the_black_bowl_on_top_of_the_cabinet_seed0_step0007_001127/LIBERO-OBJ-000030_overlay.png"
            }
          ],
          "selection_note_en": "The sample shows start and goal observations together with the end-effector path, grounding the direction question in visible trajectory evidence.",
          "provenance": {
            "dataset": "libero",
            "record_id": "LIBERO-OBJ-000030",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "LIBERO-OBJ-000008",
          "capability": "距离与接近关系",
          "capability_id": "LC4_DISTANCE_PROXIMITY",
          "capability_en": "Distance and proximity",
          "question_type": "marked_object_choice",
          "question_type_en": "Marked-object choice",
          "difficulty": "medium",
          "question_original": "以 A 为参照，哪个标注物体最靠近它？",
          "question_en": "Taking A as the reference, which labeled object is closest to it?",
          "choices_original": [
            "B. 标注物体 B",
            "C. 标注物体 C",
            "D. 标注物体 D"
          ],
          "choices_en": [
            "B. Labeled object B",
            "C. Labeled object C",
            "D. Labeled object D"
          ],
          "answer_raw": "C",
          "answer_display_original": "C. 标注物体 C",
          "answer_display_en": "C. Labeled object C",
          "images": [
            {
              "src": "assets/images/showcase/libero/03-libero-obj-000008.webp",
              "label": "RGB observation and labeled positions",
              "width": 756,
              "height": 354,
              "source_relative_path": "assets/libero_goal_put_the_wine_bottle_on_the_rack_seed0_step0003_003491/LIBERO-OBJ-000008_overlay.png"
            }
          ],
          "selection_note_en": "The labeled points are visible on a common position panel; object C is the closest marked candidate to A.",
          "provenance": {
            "dataset": "libero",
            "record_id": "LIBERO-OBJ-000008",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "LIBERO-OBJ-000005",
          "capability": "排序与区域定位",
          "capability_id": "LC5_ORDER_AND_REGION",
          "capability_en": "Spatial order and regions",
          "question_type": "ordered_list",
          "question_type_en": "Ordered list",
          "difficulty": "hard",
          "question_original": "请从标注图中判断 A、B、C、D 的从右到左排序。",
          "question_en": "From the labeled diagram, order A, B, C, and D from right to left.",
          "choices_original": [
            "A. 标注物体 A",
            "B. 标注物体 B",
            "C. 标注物体 C",
            "D. 标注物体 D"
          ],
          "choices_en": [
            "A. Labeled object A",
            "B. Labeled object B",
            "C. Labeled object C",
            "D. Labeled object D"
          ],
          "answer_raw": "[\"A\",\"D\",\"B\",\"C\"]",
          "answer_display_original": "A. 标注物体 A → D. 标注物体 D → B. 标注物体 B → C. 标注物体 C",
          "answer_display_en": "A. Labeled object A → D. Labeled object D → B. Labeled object B → C. Labeled object C",
          "images": [
            {
              "src": "assets/images/showcase/libero/04-libero-obj-000005.webp",
              "label": "RGB observation and labeled positions",
              "width": 756,
              "height": 354,
              "source_relative_path": "assets/libero_90_KITCHEN_SCENE2_put_the_black_bowl_at_the_front_on_the_plate_seed1_step0001_000745/LIBERO-OBJ-000005_overlay.png"
            }
          ],
          "selection_note_en": "All four labels and their horizontal positions are visible, so the requested right-to-left ordering can be read from the image.",
          "provenance": {
            "dataset": "libero",
            "record_id": "LIBERO-OBJ-000005",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "LIBERO-OBJ-000224",
          "capability": "数量区间与集合判断",
          "capability_id": "LC2_COUNT_AND_SET",
          "capability_en": "Object sets and task targets",
          "question_type": "multi_choice",
          "question_type_en": "Multiple choice",
          "difficulty": "hard",
          "question_original": "根据任务指令，需要重点操作哪些候选物体？可多选。\n任务指令：pick up the tomato sauce and put it in the basket",
          "question_en": "Which candidate objects are relevant to the task instruction? Select all that apply.\nTask instruction: pick up the tomato sauce and put it in the basket",
          "choices_original": [
            "A. alphabet soup",
            "B. tomato sauce",
            "C. basket",
            "D. butter"
          ],
          "choices_en": [
            "A. alphabet soup",
            "B. tomato sauce",
            "C. basket",
            "D. butter"
          ],
          "answer_raw": "[\"B\",\"C\"]",
          "answer_display_original": "B. tomato sauce + C. basket",
          "answer_display_en": "B. tomato sauce + C. basket",
          "images": [
            {
              "src": "assets/images/showcase/libero/05-libero-obj-000224.webp",
              "label": "RGB observation and task instruction",
              "width": 772,
              "height": 310,
              "source_relative_path": "assets/libero_90_LIVING_ROOM_SCENE2_pick_up_the_tomato_sauce_and_put_it_in_the_basket_seed0_step0001_002049/LIBERO-OBJ-000224_overlay.png"
            }
          ],
          "selection_note_en": "The task instruction and candidate names are both present in the artifact; the correct source and destination objects are named in the instruction.",
          "provenance": {
            "dataset": "libero",
            "record_id": "LIBERO-OBJ-000224",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "LIBERO-OBJ-000603",
          "capability": "可见对象清单与类别辨认",
          "capability_id": "LC1_OBJECT_INVENTORY",
          "capability_en": "Object inventory",
          "question_type": "yes_no",
          "question_type_en": "Yes / no",
          "difficulty": "simple",
          "question_original": "根据场景标识，plate 是否在场景中？",
          "question_en": "According to the scene view, is a plate present?",
          "choices_original": [
            "A. 是",
            "B. 否"
          ],
          "choices_en": [
            "A. Yes",
            "B. No"
          ],
          "answer_raw": "A",
          "answer_display_original": "A. 是",
          "answer_display_en": "A. Yes",
          "images": [
            {
              "src": "assets/images/showcase/libero/06-libero-obj-000603.webp",
              "label": "Scene observation",
              "width": 256,
              "height": 256,
              "source_relative_path": "assets/libero_90_KITCHEN_SCENE1_open_the_top_drawer_of_the_cabinet_and_put_the_bowl_in_it_seed2_step0005_000597/LIBERO-OBJ-000603_overlay.png"
            }
          ],
          "selection_note_en": "The plate is directly visible in the scene observation, so the yes/no answer does not depend on simulator-only state.",
          "provenance": {
            "dataset": "libero",
            "record_id": "LIBERO-OBJ-000603",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        }
      ]
    },
    {
      "dataset": "tartanground",
      "suite": "SpatialQuadruped",
      "source": "TartanGround simulated RGB observations and trajectories",
      "examples": [
        {
          "id": "TG_000064_GreatMarsh_P0000_000947_TG_C01_multi_choice_seg_category_multi_03",
          "capability_id": "TG_C01",
          "capability": "语义类别可见性",
          "capability_en": "Semantic-category visibility",
          "question_type": "multi_choice",
          "question_type_en": "Visible-category selection",
          "difficulty": "medium",
          "question_original": "哪些选项对应图中有可见像素的类别？",
          "question_en": "Which options correspond to semantic regions visible in the image? Select all that apply.",
          "choices_original": [
            "A. chair 区域",
            "B. 地面区域",
            "C. bedframe 区域",
            "D. tree 区域"
          ],
          "choices_en": [
            "A. Chair region",
            "B. Ground region",
            "C. Bed-frame region",
            "D. Tree region"
          ],
          "answer_raw": "[\"B\",\"D\"]",
          "answer_display_original": "B. 地面区域；D. tree 区域",
          "answer_display_en": "B. Ground region; D. Tree region",
          "images": [
            {
              "src": "assets/images/showcase/tartanground/tg-000064-greatmarsh-p0000-000947-tg-c01-multi-choice-seg-category-multi-03-rgb-scene-observation.webp",
              "label": "RGB scene observation",
              "width": 640,
              "height": 640,
              "source_relative_path": "assets/GreatMarsh_P0000_000947/TG_000064_GreatMarsh_P0000_000947_TG_C01_multi_choice_seg_category_multi_03_image.png"
            }
          ],
          "selection_note_en": "The RGB frame clearly shows tree canopy above a broad ground layer; the other listed categories do not fit the scene.",
          "provenance": {
            "dataset": "tartanground",
            "record_id": "TG_000064_GreatMarsh_P0000_000947_TG_C01_multi_choice_seg_category_multi_03",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "TG_000045_ConstructionSite_P0000_000033_TG_C02_single_choice_seg_area_largest_04",
          "capability_id": "TG_C02",
          "capability": "语义区域面积比较",
          "capability_en": "Visible semantic-region area",
          "question_type": "single_choice",
          "question_type_en": "Largest visible region",
          "difficulty": "medium",
          "question_original": "比较这些候选区域，哪一项的可见面积最大？",
          "question_en": "Among these candidate regions, which has the largest visible area?",
          "choices_original": [
            "A. wirespool 区域",
            "B. tarp 区域",
            "C. 地面区域",
            "D. concretecolumn 区域"
          ],
          "choices_en": [
            "A. Wire spool region",
            "B. Tarp region",
            "C. Ground region",
            "D. Concrete-column region"
          ],
          "answer_raw": "C",
          "answer_display_original": "C. 地面区域",
          "answer_display_en": "C. Ground region",
          "images": [
            {
              "src": "assets/images/showcase/tartanground/tg-000045-constructionsite-p0000-000033-tg-c02-single-choice-seg-area-largest-04-rgb-scene-observation.webp",
              "label": "RGB scene observation",
              "width": 640,
              "height": 640,
              "source_relative_path": "assets/ConstructionSite_P0000_000033/TG_000045_ConstructionSite_P0000_000033_TG_C02_single_choice_seg_area_largest_04_image.png"
            }
          ],
          "selection_note_en": "The ground occupies most of this construction-site view, with the candidate objects occupying smaller regions.",
          "provenance": {
            "dataset": "tartanground",
            "record_id": "TG_000045_ConstructionSite_P0000_000033_TG_C02_single_choice_seg_area_largest_04",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "TG_000004_ForestEnv_P0000_001183_TG_C03_grounding_choice_grid_category_location_01",
          "capability_id": "TG_C03",
          "capability": "图像区域语义定位",
          "capability_en": "Grid-based semantic localization",
          "question_type": "grounding_choice",
          "question_type_en": "Semantic grounding in a grid",
          "difficulty": "medium",
          "question_original": "如果只在 A-I 区域中选择一个，groundembankment 区域 最集中在哪一区域？",
          "question_en": "If choosing one cell from A–I, in which cell is the ground-embankment region most concentrated?",
          "choices_original": [
            "A. 标注区域 D",
            "B. 标注区域 F",
            "C. 标注区域 E",
            "D. 标注区域 A"
          ],
          "choices_en": [
            "A. Grid cell D",
            "B. Grid cell F",
            "C. Grid cell E",
            "D. Grid cell A"
          ],
          "answer_raw": "B",
          "answer_display_original": "B. 标注区域 F",
          "answer_display_en": "B. Grid cell F",
          "images": [
            {
              "src": "assets/images/showcase/tartanground/tg-000004-forestenv-p0000-001183-tg-c03-grounding-choice-grid-category-location-01-grid-labeled-rgb-scene.webp",
              "label": "Grid-labeled RGB scene",
              "width": 640,
              "height": 640,
              "source_relative_path": "assets/ForestEnv_P0000_001183/TG_000004_ForestEnv_P0000_001183_TG_C03_grounding_choice_grid_category_location_01_grid_overlay.jpg"
            },
            {
              "src": "assets/images/showcase/tartanground/tg-000004-forestenv-p0000-001183-tg-c03-grounding-choice-grid-category-location-01-unmarked-rgb-scene.webp",
              "label": "Unmarked RGB scene",
              "width": 640,
              "height": 640,
              "source_relative_path": "assets/ForestEnv_P0000_001183/TG_000004_ForestEnv_P0000_001183_TG_C03_grounding_choice_grid_category_location_01_aux_base.png"
            }
          ],
          "selection_note_en": "The A–I grid is drawn over the scene, and the ground-embankment region is most concentrated in cell F.",
          "provenance": {
            "dataset": "tartanground",
            "record_id": "TG_000004_ForestEnv_P0000_001183_TG_C03_grounding_choice_grid_category_location_01",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "TG_000007_JapaneseAlley_P0000_000792_TG_C06_multi_view_choice_multiview_category_area_08",
          "capability_id": "TG_C06",
          "capability": "多视角语义一致性",
          "capability_en": "Semantic comparison across synchronized views",
          "question_type": "multi_view_choice",
          "question_type_en": "Multi-view area comparison",
          "difficulty": "hard",
          "question_original": "请从标注视图中选择 sign 区域 占比最高的一项。",
          "question_en": "Which labeled view shows the largest visible sign region?",
          "choices_original": [
            "A. 标注视图 V",
            "B. 标注视图 W",
            "C. 标注视图 X",
            "D. 标注视图 Y"
          ],
          "choices_en": [
            "A. View V",
            "B. View W",
            "C. View X",
            "D. View Y"
          ],
          "answer_raw": "C",
          "answer_display_original": "C. 标注视图 X",
          "answer_display_en": "C. View X",
          "images": [
            {
              "src": "assets/images/showcase/tartanground/tg-000007-japanesealley-p0000-000792-tg-c06-multi-view-choice-multiview-category-area-08-four-view-comparison-strip.webp",
              "label": "Four-view comparison strip",
              "width": 1200,
              "height": 300,
              "source_relative_path": "assets/JapaneseAlley_P0000_000792/TG_000007_JapaneseAlley_P0000_000792_TG_C06_multi_view_choice_multiview_category_area_08_multiview_overlay.jpg"
            },
            {
              "src": "assets/images/showcase/tartanground/tg-000007-japanesealley-p0000-000792-tg-c06-multi-view-choice-multiview-category-area-08-view-v.webp",
              "label": "View V",
              "width": 640,
              "height": 640,
              "source_relative_path": "assets/JapaneseAlley_P0000_000792/TG_000007_JapaneseAlley_P0000_000792_TG_C06_multi_view_choice_multiview_category_area_08_aux_V.png"
            },
            {
              "src": "assets/images/showcase/tartanground/tg-000007-japanesealley-p0000-000792-tg-c06-multi-view-choice-multiview-category-area-08-view-w.webp",
              "label": "View W",
              "width": 640,
              "height": 640,
              "source_relative_path": "assets/JapaneseAlley_P0000_000792/TG_000007_JapaneseAlley_P0000_000792_TG_C06_multi_view_choice_multiview_category_area_08_aux_W.png"
            },
            {
              "src": "assets/images/showcase/tartanground/tg-000007-japanesealley-p0000-000792-tg-c06-multi-view-choice-multiview-category-area-08-view-x.webp",
              "label": "View X",
              "width": 640,
              "height": 640,
              "source_relative_path": "assets/JapaneseAlley_P0000_000792/TG_000007_JapaneseAlley_P0000_000792_TG_C06_multi_view_choice_multiview_category_area_08_aux_X.png"
            },
            {
              "src": "assets/images/showcase/tartanground/tg-000007-japanesealley-p0000-000792-tg-c06-multi-view-choice-multiview-category-area-08-view-y.webp",
              "label": "View Y",
              "width": 640,
              "height": 640,
              "source_relative_path": "assets/JapaneseAlley_P0000_000792/TG_000007_JapaneseAlley_P0000_000792_TG_C06_multi_view_choice_multiview_category_area_08_aux_Y.png"
            }
          ],
          "selection_note_en": "The four synchronized views are shown together and separately; the sign region is largest in view X.",
          "provenance": {
            "dataset": "tartanground",
            "record_id": "TG_000007_JapaneseAlley_P0000_000792_TG_C06_multi_view_choice_multiview_category_area_08",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "TG_000012_Downtown_P0000_000285_TG_C07_temporal_choice_trajectory_stage_interval_05",
          "capability_id": "TG_C07",
          "capability": "轨迹顺序与路径结构",
          "capability_en": "Trajectory order and route structure",
          "question_type": "temporal_choice",
          "question_type_en": "Trajectory-stage classification",
          "difficulty": "simple",
          "question_original": "观察 S-G 轨迹，A 属于哪个阶段区间？",
          "question_en": "Along the S-to-G trajectory, which stage interval contains point A?",
          "choices_original": [
            "A. 中段",
            "B. 前段",
            "C. 后段"
          ],
          "choices_en": [
            "A. Middle stage",
            "B. Early stage",
            "C. Late stage"
          ],
          "answer_raw": "B",
          "answer_display_original": "B. 前段",
          "answer_display_en": "B. Early stage",
          "images": [
            {
              "src": "assets/images/showcase/tartanground/tg-000012-downtown-p0000-000285-tg-c07-temporal-choice-trajectory-stage-interval-05-trajectory-overlay.webp",
              "label": "Trajectory overlay",
              "width": 900,
              "height": 680,
              "source_relative_path": "assets/Downtown_P0000_000285/TG_000012_Downtown_P0000_000285_TG_C07_temporal_choice_trajectory_stage_interval_05_trajectory_overlay.jpg"
            }
          ],
          "selection_note_en": "The route is marked from S to G, and point A lies close to the start of the path.",
          "provenance": {
            "dataset": "tartanground",
            "record_id": "TG_000012_Downtown_P0000_000285_TG_C07_temporal_choice_trajectory_stage_interval_05",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "TG_000106_OldIndustrialCity_P0000_000201_TG_C08_single_choice_motion_turn_choice_09",
          "capability_id": "TG_C08",
          "capability": "运动趋势与朝向变化",
          "capability_en": "Motion trend and heading change",
          "question_type": "single_choice",
          "question_type_en": "Trajectory turn direction",
          "difficulty": "medium",
          "question_original": "观察轨迹方向变化，这段路径主要是在向哪边转？",
          "question_en": "Observe the change in trajectory direction. Which way does this path mainly turn?",
          "choices_original": [
            "A. 基本直行",
            "B. 主要向右转",
            "C. 主要向左转"
          ],
          "choices_en": [
            "A. Mostly straight",
            "B. Mainly turns right",
            "C. Mainly left"
          ],
          "answer_raw": "B",
          "answer_display_original": "B. 主要向右转",
          "answer_display_en": "B. Mainly turns right",
          "images": [
            {
              "src": "assets/images/showcase/tartanground/tg-000106-oldindustrialcity-p0000-000201-tg-c08-single-choice-motion-turn-choice-09-trajectory-overlay.webp",
              "label": "Trajectory overlay",
              "width": 900,
              "height": 680,
              "source_relative_path": "assets/OldIndustrialCity_P0000_000201/TG_000106_OldIndustrialCity_P0000_000201_TG_C08_single_choice_motion_turn_choice_09_trajectory_overlay.jpg"
            }
          ],
          "selection_note_en": "The route runs from S to G and makes a pronounced rightward change through the labeled intermediate points.",
          "provenance": {
            "dataset": "tartanground",
            "record_id": "TG_000106_OldIndustrialCity_P0000_000201_TG_C08_single_choice_motion_turn_choice_09",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        }
      ]
    },
    {
      "dataset": "uav",
      "suite": "SpatialUAV",
      "source": "Real aerial imagery",
      "examples": [
        {
          "id": "UAV_007453_img_2646_UAV_C08_marked_object_choice_marked_category_choice_05",
          "capability_id": "UAV_C08",
          "capability": "类别约束下的对象选择与匹配",
          "capability_en": "Category matching for a marked object",
          "question_type": "marked_object_choice",
          "question_type_en": "Marked-object classification",
          "difficulty": "simple",
          "question_original": "请判断标注物体 A 是什么类别。",
          "question_en": "Select the category of labeled object A from the options.",
          "choices_original": [
            "A. wind turbine",
            "B. building",
            "C. track",
            "D. barrier"
          ],
          "choices_en": [
            "A. Wind turbine",
            "B. Building",
            "C. Track",
            "D. Barrier"
          ],
          "answer_raw": "C",
          "answer_display_original": "C. track",
          "answer_display_en": "C. Track",
          "images": [
            {
              "src": "assets/images/showcase/uav/uav-007453-img-2646-uav-c08-marked-object-choice-marked-category-choice-05-a-marked-aerial-image.webp",
              "label": "A-marked aerial image",
              "width": 1400,
              "height": 1050,
              "source_relative_path": "assets/img_2646/UAV_007453_img_2646_UAV_C08_marked_object_choice_marked_category_choice_05_bbox_overlay.jpg"
            },
            {
              "src": "assets/images/showcase/uav/uav-007453-img-2646-uav-c08-marked-object-choice-marked-category-choice-05-unmarked-aerial-image.webp",
              "label": "Unmarked aerial image",
              "width": 1400,
              "height": 1050,
              "source_relative_path": "assets/img_2646/img_2646_base.jpg"
            }
          ],
          "selection_note_en": "The center marker for A lands on the running lanes, and the labeled region is large enough to read the target in its aerial context.",
          "provenance": {
            "dataset": "uav",
            "record_id": "UAV_007453_img_2646_UAV_C08_marked_object_choice_marked_category_choice_05",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "UAV_005797_img_2718_UAV_C08_marked_object_choice_marked_category_choice_07",
          "capability_id": "UAV_C08",
          "capability": "类别约束下的对象选择与匹配",
          "capability_en": "Category matching for a marked object",
          "question_type": "marked_object_choice",
          "question_type_en": "Marked-object classification",
          "difficulty": "simple",
          "question_original": "从候选类别中选出标注物体 A 的类别。",
          "question_en": "Choose the category of labeled object A.",
          "choices_original": [
            "A. lane",
            "B. car",
            "C. motorcycle",
            "D. person"
          ],
          "choices_en": [
            "A. Lane",
            "B. Car",
            "C. Motorcycle",
            "D. Person"
          ],
          "answer_raw": "B",
          "answer_display_original": "B. car",
          "answer_display_en": "B. Car",
          "images": [
            {
              "src": "assets/images/showcase/uav/uav-005797-img-2718-uav-c08-marked-object-choice-marked-category-choice-07-a-marked-aerial-image.webp",
              "label": "A-marked aerial image",
              "width": 1400,
              "height": 1050,
              "source_relative_path": "assets/img_2718/UAV_005797_img_2718_UAV_C08_marked_object_choice_marked_category_choice_07_bbox_overlay.jpg"
            },
            {
              "src": "assets/images/showcase/uav/uav-005797-img-2718-uav-c08-marked-object-choice-marked-category-choice-07-unmarked-aerial-image.webp",
              "label": "Unmarked aerial image",
              "width": 1400,
              "height": 1050,
              "source_relative_path": "assets/img_2718/img_2718_base.jpg"
            }
          ],
          "selection_note_en": "The center marker inside box A falls on a parked passenger car; the lane, motorcycle, and person distractors are visually distinct.",
          "provenance": {
            "dataset": "uav",
            "record_id": "UAV_005797_img_2718_UAV_C08_marked_object_choice_marked_category_choice_07",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "UAV_002782_img_1412_UAV_C03_grounding_choice_object_grid_region_01",
          "capability_id": "UAV_C03",
          "capability": "图像平面位置与区域定位",
          "capability_en": "Image-plane localization",
          "question_type": "grounding_choice",
          "question_type_en": "Spatial grounding",
          "difficulty": "simple",
          "question_original": "从九宫格选项中选出标注物体 A 的位置。",
          "question_en": "Which grid cell contains marked object A?",
          "choices_original": [
            "A. 左上",
            "B. 上中",
            "C. 右上",
            "D. 左中",
            "E. 中心",
            "F. 右中",
            "G. 左下",
            "H. 下中",
            "I. 右下"
          ],
          "choices_en": [
            "A. Top-left",
            "B. Top-center",
            "C. Top-right",
            "D. Middle-left",
            "E. Center",
            "F. Middle-right",
            "G. Bottom-left",
            "H. Bottom-center",
            "I. Bottom-right"
          ],
          "answer_raw": "E",
          "answer_display_original": "E. 中心",
          "answer_display_en": "E. Center",
          "images": [
            {
              "src": "assets/images/showcase/uav/uav-002782-img-1412-uav-c03-grounding-choice-object-grid-region-01-a-marked-aerial-image.webp",
              "label": "A-marked aerial image",
              "width": 1400,
              "height": 788,
              "source_relative_path": "assets/img_1412/UAV_002782_img_1412_UAV_C03_grounding_choice_object_grid_region_01_bbox_overlay.jpg"
            },
            {
              "src": "assets/images/showcase/uav/uav-002782-img-1412-uav-c03-grounding-choice-object-grid-region-01-unmarked-aerial-image.webp",
              "label": "Unmarked aerial image",
              "width": 1400,
              "height": 788,
              "source_relative_path": "assets/img_1412/img_1412_base.jpg"
            }
          ],
          "selection_note_en": "The marked lane’s centroid lies in the center cell; the large outlined target makes its image-plane location readable.",
          "provenance": {
            "dataset": "uav",
            "record_id": "UAV_002782_img_1412_UAV_C03_grounding_choice_object_grid_region_01",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "UAV_000005_img_3206_UAV_C04_single_choice_largest_area_06",
          "capability_id": "UAV_C04",
          "capability": "可见面积与显著尺度",
          "capability_en": "Visible area and apparent scale",
          "question_type": "single_choice",
          "question_type_en": "Largest visible area",
          "difficulty": "medium",
          "question_original": "哪个标注框对应的实例可见区域最多？",
          "question_en": "Which labeled instance occupies the largest visible area?",
          "choices_original": [
            "A. 标注物体 A",
            "B. 标注物体 B",
            "C. 标注物体 C",
            "D. 标注物体 D"
          ],
          "choices_en": [
            "A. Marked object A",
            "B. Marked object B",
            "C. Marked object C",
            "D. Marked object D"
          ],
          "answer_raw": "C",
          "answer_display_original": "C. 标注物体 C",
          "answer_display_en": "C. Marked object C",
          "images": [
            {
              "src": "assets/images/showcase/uav/uav-000005-img-3206-uav-c04-single-choice-largest-area-06-annotated-aerial-image.webp",
              "label": "Annotated aerial image",
              "width": 1800,
              "height": 1013,
              "source_relative_path": "assets/img_3206/UAV_000005_img_3206_UAV_C04_single_choice_largest_area_06_bbox_overlay.jpg"
            },
            {
              "src": "assets/images/showcase/uav/uav-000005-img-3206-uav-c04-single-choice-largest-area-06-unmarked-aerial-image.webp",
              "label": "Unmarked aerial image",
              "width": 1800,
              "height": 1013,
              "source_relative_path": "assets/img_3206/img_3206_base.jpg"
            }
          ],
          "selection_note_en": "The C-labeled street region covers substantially more of the frame than the other outlined instances.",
          "provenance": {
            "dataset": "uav",
            "record_id": "UAV_000005_img_3206_UAV_C04_single_choice_largest_area_06",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "UAV_007691_img_5216_UAV_C07_matching_boundary_side_matching_04",
          "capability_id": "UAV_C07",
          "capability": "框几何形状与图像边界关系",
          "capability_en": "Bounding-box geometry and image-boundary relations",
          "question_type": "matching",
          "question_type_en": "Nearest image edge matching",
          "difficulty": "hard",
          "question_original": "按标注框到图像边界的距离完成匹配。",
          "question_en": "Match each labeled object to its nearest image edge.",
          "choices_original": [
            "A. 左边缘",
            "B. 右边缘",
            "C. 上边缘",
            "D. 下边缘"
          ],
          "choices_en": [
            "A. Left edge",
            "B. Right edge",
            "C. Top edge",
            "D. Bottom edge"
          ],
          "answer_raw": "{\"A\":\"B\",\"B\":\"D\",\"C\":\"C\"}",
          "answer_display_original": "A → 右边缘；B → 下边缘；C → 上边缘",
          "answer_display_en": "A → right edge; B → bottom edge; C → top edge",
          "images": [
            {
              "src": "assets/images/showcase/uav/uav-007691-img-5216-uav-c07-matching-boundary-side-matching-04-annotated-aerial-image.webp",
              "label": "Annotated aerial image",
              "width": 1800,
              "height": 1012,
              "source_relative_path": "assets/img_5216/UAV_007691_img_5216_UAV_C07_matching_boundary_side_matching_04_bbox_overlay.jpg"
            },
            {
              "src": "assets/images/showcase/uav/uav-007691-img-5216-uav-c07-matching-boundary-side-matching-04-unmarked-aerial-image.webp",
              "label": "Unmarked aerial image",
              "width": 1800,
              "height": 1012,
              "source_relative_path": "assets/img_5216/img_5216_base.jpg"
            }
          ],
          "selection_note_en": "The A, B, and C boxes are separated across the right, lower, and top edges, so each requested relation is visible in the frame.",
          "provenance": {
            "dataset": "uav",
            "record_id": "UAV_007691_img_5216_UAV_C07_matching_boundary_side_matching_04",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        },
        {
          "id": "UAV_000313_img_5188_UAV_C01_multi_choice_category_visible_multi_05",
          "capability_id": "UAV_C01",
          "capability": "可见类别识别",
          "capability_en": "Visible object-category recognition",
          "question_type": "multi_choice",
          "question_type_en": "Visible-category selection",
          "difficulty": "medium",
          "question_original": "从候选项中选择当前图像中可见的物体类别，可多选。",
          "question_en": "Which object categories are visible in this aerial image? Select all that apply.",
          "choices_original": [
            "A. door",
            "B. roof",
            "C. boat",
            "D. bridge",
            "E. trees"
          ],
          "choices_en": [
            "A. Door",
            "B. Roof",
            "C. Boat",
            "D. Bridge",
            "E. Trees"
          ],
          "answer_raw": "[\"B\",\"C\",\"E\"]",
          "answer_display_original": "B. roof；C. boat；E. trees",
          "answer_display_en": "B. Roof; C. Boat; E. Trees",
          "images": [
            {
              "src": "assets/images/showcase/uav/uav-000313-img-5188-uav-c01-multi-choice-category-visible-multi-05-aerial-rgb-image.webp",
              "label": "Aerial RGB image",
              "width": 1800,
              "height": 1012,
              "source_relative_path": "assets/img_5188/UAV_000313_img_5188_UAV_C01_multi_choice_category_visible_multi_05_image.jpg"
            }
          ],
          "selection_note_en": "The aerial frame shows rooftops along the bank, trees, and a small but clearly visible boat on the water.",
          "provenance": {
            "dataset": "uav",
            "record_id": "UAV_000313_img_5188_UAV_C01_multi_choice_category_visible_multi_05",
            "source_jsonl": "data/test-00000-of-00001.jsonl"
          }
        }
      ]
    }
  ]
};
