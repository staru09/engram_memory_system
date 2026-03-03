# Result Summary — Scale Evaluation (All Stages)

Consolidated query results across all 4 stages for easy verification.

---

# Stage 1 — Baseline (100 msgs, query time: 2026-02-01)

| Metric | Value |
|--------|-------|
| MemCells | 10 |
| MemScenes | 1 |
| Active Facts | 108 |
| Total Facts | 108 |
| Conflicts | 0 |
| Dedup Rate | 0.0% |
| Queries Sufficient | 6/6 (100%) |
| Avg Latency | 14.6s |

---

## Q1: What does Aru study and where?
**Category:** Basic Extraction
**Expected:** Junior in Mechanical Engineering at Stanford. Taking ME 101 (thermodynamics) and ME 131 (fluid mechanics).
**Status:** SUFFICIENT | Rounds: 1 | Latency: 14.4s
**Retrieved:** 10 episodes, 10 facts, 37 active foresight

**Generated Answer:** Aru studies engineering at Stanford University, specifically focusing on ME 101 (thermodynamics) and ME 131 (fluid mechanics). In addition to their coursework, they are working on a competition robotics project and reside at Wilbur Hall.

---

## Q2: What kind of diet does Aru follow?
**Category:** Basic Extraction
**Expected:** Strict vegetarian. Cooks Indian food (dal, sabzi, paneer). Never eats meat, chicken, or fish.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 11.8s
**Retrieved:** 10 episodes, 10 facts, 37 active foresight

**Generated Answer:** Aru follows a strict vegetarian diet that excludes meat, chicken, and fish. To maintain this regimen, Aru prepares Indian meals such as dal, vegetable biryani, and aloo parathas at Wilbur Hall.

---

## Q3: Where does Aru currently live?
**Category:** Basic Extraction
**Expected:** Wilbur Hall, on-campus at Stanford.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 13.2s
**Retrieved:** 10 episodes, 10 facts, 37 active foresight

**Generated Answer:** Aru currently lives at Wilbur Hall. While residing there, Aru maintains a strict vegetarian diet by preparing Indian meals in the hall's kitchen.

---

## Q4: What are Aru's hobbies and interests?
**Category:** Scene Formation
**Expected:** Basketball (Arrillaga gym weekends), acoustic guitar, robotics club (building competition bot). Runs 3 miles daily.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 18.0s
**Retrieved:** 10 episodes, 10 facts, 37 active foresight

**Generated Answer:** Aru enjoys building competition robotics, playing fingerstyle folk songs on the acoustic guitar, and cooking vegetarian Indian meals like dal and vegetable biryani. They also maintain an active lifestyle through daily three-mile runs and playing weekend basketball at Arrillaga gym. Additionally, Aru is interested in outdoor activities such as hiking and camping, specifically planning trips to Yosemite to explore trails like Half Dome and the Mist Trail.

---

## Q5: Does Aru have any upcoming travel plans?
**Category:** Scene Formation
**Expected:** Spring break trip to Yosemite planned with friend Jake in late March.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 12.1s
**Retrieved:** 10 episodes, 10 facts, 37 active foresight

**Generated Answer:** Aru is planning an upcoming spring break trip to Yosemite with a friend named Jake. During this trip, they plan to go hiking and camping, and Aru intends to play folk songs on an acoustic guitar by the campfire.

---

## Q6: What do you know about Aru overall?
**Category:** Simple Retrieval
**Expected:** Stanford ME junior, lives in Wilbur Hall, strict vegetarian, plays basketball and guitar, in robotics club, planning Yosemite trip.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 18.2s
**Retrieved:** 10 episodes, 10 facts, 37 active foresight

**Generated Answer:** Aru is an engineering student residing at Wilbur Hall who balances thermodynamics and fluid mechanics coursework with a competition robotics project. To maintain peak health, they follow a strict vegetarian Indian diet, run three miles daily, and play weekend basketball at the Arrillaga gym. Aru also practices acoustic guitar in preparation for an upcoming spring break trip to Yosemite with their friend Jake.

---
---

# Stage 2 — Conflicts + Foresight (200 msgs cumulative, query time: 2026-03-01)

| Metric | Value |
|--------|-------|
| MemCells | 19 |
| MemScenes | 2 |
| Active Facts | 200 |
| Total Facts | 220 |
| Conflicts | 20 |
| Dedup Rate | 9.1% |
| Queries Sufficient | 8/10 (80%) |
| Avg Latency | 18.6s |

---

## Q1: What kind of diet does Aru currently follow?
**Category:** Conflict Detection
**Expected:** No longer vegetarian. Now eats chicken and fish (doctor's orders for ACL recovery protein). Conflict should supersede vegetarian.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 13.0s
**Retrieved:** 10 episodes, 5 facts, 72 active foresight

**Generated Answer:** Aru recently switched from a strict vegetarian diet to a high-protein diet that includes chicken and fish. This change was made to assist in recovering from a torn ACL sustained in February 2026.

---

## Q2: Where is Aru currently working or interning?
**Category:** Conflict Detection
**Expected:** No internship yet. Still a student but pivoted from ME to CS/ML (taking CS 229).
**Status:** INSUFFICIENT | Rounds: 2 | Latency: 31.0s
**Retrieved:** 10 episodes, 12 facts, 72 active foresight

**Generated Answer:** No relevant information found.

---

## Q3: What is Aru's academic and internship history?
**Category:** Profile Evolution
**Expected:** Was ME student (ME 101, ME 131). Dropped ME 131, now taking CS 229 (Machine Learning). Pivoting to CS/ML.
**Status:** INSUFFICIENT | Rounds: 2 | Latency: 33.9s
**Retrieved:** 10 episodes, 10 facts, 72 active foresight

**Generated Answer:** Aru is an engineering student at Stanford University whose academic history includes coursework in ME 101, thermodynamics, and ME 131, though they recently shifted focus to CS 229 (Machine Learning) and a competition robotics project. No relevant information was found regarding Aru's internship history.

---

## Q4: Does Aru have any active health issues or injuries?
**Category:** Foresight / Temporal
**Expected:** Torn ACL (Feb 20). Doing physical therapy 3x/week. No sports for 2 months (until April 20).
**Status:** SUFFICIENT | Rounds: 1 | Latency: 15.1s
**Retrieved:** 10 episodes, 6 facts, 72 active foresight

**Generated Answer:** Aru is currently recovering from a torn ACL sustained while playing basketball on February 20, 2026. To manage this injury, Aru is attending physical therapy three times a week, taking anti-inflammatory medication, and is restricted from sports and heavy physical activity until April 20, 2026. Additionally, Aru has switched to a diet including chicken and fish to aid in muscle recovery.

---

## Q5: Is Aru currently on any medication?
**Category:** Foresight / Temporal
**Expected:** Anti-inflammatory meds (started Feb 20, prescribed 2 weeks until March 6). Should still be active at query time March 1.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 13.7s
**Retrieved:** 10 episodes, 6 facts, 72 active foresight

**Generated Answer:** Aru is currently taking anti-inflammatory medication as part of the recovery process for a torn ACL sustained while playing basketball. This medication course is scheduled to continue until March 6, 2026.

---

## Q6: What are Aru's hobbies and interests?
**Category:** Retrieval Relevance
**Expected:** Robotics club (still active). Guitar (still active, not contradicted). No basketball (ACL injury). Can't run or do sports.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 18.2s
**Retrieved:** 10 episodes, 7 facts, 72 active foresight

**Generated Answer:** Aru is interested in robotics and engineering, specifically focusing on building competition bots and studying machine learning. Their hobbies include playing folk songs on the acoustic guitar, cooking vegetarian Indian meals, and engaging in physical activities like daily running, basketball, and hiking. Aru also enjoys traveling, with planned trips to explore Yosemite and Tokyo.

---

## Q7: Does Aru have any upcoming travel plans?
**Category:** Retrieval Relevance
**Expected:** Yosemite cancelled due to injury. Planning summer trip to Tokyo instead.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 12.9s
**Retrieved:** 10 episodes, 7 facts, 72 active foresight

**Generated Answer:** Aru is planning a summer trip to Tokyo, where they intend to explore Shibuya, Shinjuku, and Akihabara. While there were plans for a spring break trip to Yosemite with Jake, that trip was canceled following Aru's recent ACL injury.

---

## Q8: Where does Aru currently live?
**Category:** Profile Evolution
**Expected:** University Ave apartment (off-campus) with Jake. No longer in Wilbur Hall. Conflict should supersede.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 16.1s
**Retrieved:** 10 episodes, 6 facts, 72 active foresight

**Generated Answer:** Aru currently lives in an apartment on University Avenue with his roommate, Jake. He recently moved there from Wilbur Hall to assist with his recovery from a torn ACL sustained in February 2026.

---

## Q9: Can Aru play sports this week?
**Category:** Foresight / Temporal
**Expected:** No. Torn ACL, doctor restricted all sports for 2 months (until April 20). Cannot run or play basketball.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 14.9s
**Retrieved:** 10 episodes, 8 facts, 72 active foresight

**Generated Answer:** No, Aru cannot play sports this week as they are currently recovering from a torn ACL sustained on February 20, 2026. According to the active foresight, Aru is prohibited from participating in sports or heavy physical activity until April 20, 2026.

---

## Q10: What do you know about Aru overall?
**Category:** Retrieval Relevance
**Expected:** Stanford student, torn ACL recovery, pivoted to CS/ML, moved to University Ave, no longer vegetarian, Tokyo trip planned.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 16.7s
**Retrieved:** 10 episodes, 6 facts, 72 active foresight

**Generated Answer:** Aru is a student currently recovering from a torn ACL sustained in February 2026, which prompted a move to University Ave and a diet change from vegetarianism to eating chicken and fish. Currently focusing on CS 229 (Machine Learning), Aru is planning a future trip to Tokyo while navigating a recovery regimen that includes medication and physical therapy. Previously, Aru lived at Wilbur Hall and balanced engineering coursework with robotics projects, daily running, and playing the acoustic guitar.

---
---

# Stage 3 — Foresight Expiry + Evolution (300 msgs cumulative, query time: 2026-05-01)

| Metric | Value |
|--------|-------|
| MemCells | 29 |
| MemScenes | 2 |
| Active Facts | 313 |
| Total Facts | 333 |
| Conflicts | 20 |
| Dedup Rate | 6.0% |
| Queries Sufficient | 10/10 (100%) |
| Avg Latency | 23.5s |

---

## Q1: What kind of diet does Aru currently follow?
**Category:** Conflict Detection
**Expected:** Still eating chicken and fish (not vegetarian). No new diet change in stage 3.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 17.0s
**Retrieved:** 10 episodes, 6 facts, 56 active foresight

**Generated Answer:** Aru currently follows a non-vegetarian diet that incorporates chicken and fish to provide protein for muscle rebuilding and recovery. This high-protein diet includes meals such as salmon and chicken, which he prepares at his apartment with help from his roommate, Jake.

---

## Q2: Where is Aru currently working or interning?
**Category:** Conflict Detection
**Expected:** Summer research internship at Stanford AI Lab (SAIL) under Prof. Fei-Fei Li. Computer vision for robotics. Starting June.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 24.5s
**Retrieved:** 10 episodes, 10 facts, 56 active foresight

**Generated Answer:** Aru is currently leading a robotics software team, where he is training a successor and documenting the perception stack. He is scheduled to begin a research internship at the Stanford Artificial Intelligence Laboratory (SAIL) in June 2026.

---

## Q3: What is Aru's academic and internship history?
**Category:** Profile Evolution
**Expected:** ME student → dropped ME 131 → CS 229 pivot → SAIL internship (computer vision for robotics).
**Status:** SUFFICIENT | Rounds: 1 | Latency: 33.4s
**Retrieved:** 10 episodes, 10 facts, 56 active foresight

**Generated Answer:** Aru is a Stanford University student currently enrolled in CS 229 (Machine Learning), where he is working on an NBA sports analytics project using reinforcement learning. He currently leads a robotics software team and is preparing to start a research internship at the Stanford Artificial Intelligence Laboratory (SAIL) in June 2026 under Professor Fei-Fei Li, focusing on computer vision for robotics.

---

## Q4: Does Aru have any active health issues or injuries?
**Category:** Foresight / Temporal
**Expected:** No. ACL fully healed (cleared April 20). Stopped physical therapy. Running again. No injuries.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 16.9s
**Retrieved:** 10 episodes, 8 facts, 56 active foresight

**Generated Answer:** Aru currently has no active health issues or injuries and is in 100% health. He has fully recovered from a previous torn ACL and has returned to peak physical condition. He is now active in high-intensity sports, including playing basketball and bouldering at a V5 level.

---

## Q5: Is Aru currently on any medication?
**Category:** Foresight / Temporal
**Expected:** No. Anti-inflammatory meds expired (ended March 6). No current medication.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 16.2s
**Retrieved:** 10 episodes, 8 facts, 56 active foresight

**Generated Answer:** Aru is not currently on any medication. Having fully recovered from a previous ACL injury, he has discontinued all related medications and is now considered 100% healthy.

---

## Q6: What are Aru's hobbies and interests?
**Category:** Retrieval Relevance
**Expected:** Bouldering (new), robotics club (leads software/perception team). Quit guitar. Back to running. Basketball possible again.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 23.3s
**Retrieved:** 10 episodes, 8 facts, 56 active foresight

**Generated Answer:** Aru's primary hobbies include bouldering at a V5 level and playing basketball. Their interests are heavily focused on robotics and machine learning, specifically computer vision for robotic manipulation and reinforcement learning for NBA sports analytics. Aru also enjoys exploring technology, electronics, and arcades, which are central themes of their upcoming travel plans.

---

## Q7: Does Aru have any upcoming travel plans?
**Category:** Retrieval Relevance
**Expected:** Tokyo trip confirmed for August. Flights and Airbnb booked.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 19.5s
**Retrieved:** 10 episodes, 9 facts, 56 active foresight

**Generated Answer:** Aru is planning a trip to Tokyo, Japan, in August 2026 with his roommate Jake. The itinerary includes visiting robotics exhibits in Akihabara, exploring districts like Shibuya and Shinjuku, and bouldering at various gyms and outdoor locations such as Mitake. They have already booked flights and an Airbnb for this celebratory trip.

---

## Q8: Where does Aru currently live?
**Category:** Profile Evolution
**Expected:** Still on University Ave apartment with Jake.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 17.3s
**Retrieved:** 10 episodes, 7 facts, 56 active foresight

**Generated Answer:** Aru currently lives in an apartment on University Avenue. They share this residence with their roommate and friend, Jake.

---

## Q9: Can Aru play sports this week?
**Category:** Foresight / Temporal
**Expected:** Yes. ACL fully healed, doctor cleared, back to running and basketball.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 43.6s
**Retrieved:** 10 episodes, 9 facts, 56 active foresight

**Generated Answer:** Yes, Aru can play sports this week as they are currently 100% healthy and have fully recovered from a previous ACL injury. Aru has already resumed playing basketball and is actively bouldering at a V5 level at the campus climbing wall.

---

## Q10: What do you know about Aru overall?
**Category:** Retrieval Relevance
**Expected:** Stanford CS/ML student, ACL healed, SAIL internship, bouldering, quit guitar, lives on University Ave, Tokyo trip in August.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 23.0s
**Retrieved:** 10 episodes, 7 facts, 56 active foresight

**Generated Answer:** Aru is a Stanford University student living on University Ave who has fully recovered from an ACL injury and currently leads a robotics software team. He is an active V5-level boulderer and is preparing for a computer vision research internship at the Stanford Artificial Intelligence Laboratory starting in June 2026. Aru follows a non-vegetarian diet and is planning an extensive trip to Tokyo in August 2026 with his roommate, Jake.

---
---

# Stage 4 — Scale + Noise (500 msgs cumulative, query time: 2026-06-15)

| Metric | Value |
|--------|-------|
| MemCells | 45 |
| MemScenes | 3 |
| Active Facts | 445 |
| Total Facts | 467 |
| Conflicts | 22 |
| Dedup Rate | 4.7% |
| Queries Sufficient | 10/10 (100%) |
| Avg Latency | 26.2s |

---

## Q1: What kind of diet does Aru currently follow?
**Category:** Conflict Detection
**Expected:** Fully non-vegetarian. Meal-preps chicken and rice. No change from stage 2+ but reinforced.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 23.4s
**Retrieved:** 10 episodes, 10 facts, 93 active foresight

**Generated Answer:** Aru currently follows a non-vegetarian diet that includes chicken and fish to support recovery and muscle building. They regularly meal-prep chicken and rice and plan to participate in non-vegetarian food tours during an upcoming trip to Tokyo.

---

## Q2: Where is Aru currently working or interning?
**Category:** Conflict Detection
**Expected:** Tesla Autopilot team. Left SAIL. Conflict should supersede SAIL internship.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 23.0s
**Retrieved:** 10 episodes, 10 facts, 93 active foresight

**Generated Answer:** Aru is currently an intern at Tesla Autopilot in Palo Alto, a position they started in approximately May 2026. This role follows their previous work at the Stanford Artificial Intelligence Laboratory (SAIL), which they left about six weeks prior to mid-June 2026.

---

## Q3: What is Aru's academic and internship history?
**Category:** Profile Evolution
**Expected:** ME courses → CS 229 pivot → SAIL research → Tesla Autopilot. Full progression visible.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 23.8s
**Retrieved:** 10 episodes, 10 facts, 93 active foresight

**Generated Answer:** Aru is a Stanford University student currently enrolled in CS 229 (Machine Learning) who previously led a robotics software team and completed an NBA sports analytics project. Their internship history includes a research position at the Stanford Artificial Intelligence Laboratory (SAIL) and a current role at Tesla Autopilot in Palo Alto.

---

## Q4: Does Aru have any active health issues or injuries?
**Category:** Foresight / Temporal
**Expected:** Food poisoning (June 10). On antibiotics for 5 days (until June 15). Depends on query time whether active.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 22.9s
**Retrieved:** 10 episodes, 10 facts, 93 active foresight

**Generated Answer:** Aru is currently 100% healthy and has no active injuries. They have fully recovered from a previous ACL injury and completed a course of antibiotics for food poisoning as of June 15, 2026.

---

## Q5: Is Aru currently on any medication?
**Category:** Foresight / Temporal
**Expected:** Antibiotics for food poisoning (5 days, June 10-15). Should be active/expiring at query time June 15.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 23.7s
**Retrieved:** 10 episodes, 10 facts, 93 active foresight

**Generated Answer:** Aru is currently not taking any medication. As of June 15, 2026, Aru completed a course of antibiotics for food poisoning and had previously discontinued all medications related to an ACL injury. Therefore, Aru is currently considered off all medications and at 100% health.

---

## Q6: What are Aru's hobbies and interests?
**Category:** Retrieval Relevance
**Expected:** Surfing at Pacifica (new), bouldering (occasional), robotics club. No guitar. No basketball mentioned recently.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 27.1s
**Retrieved:** 10 episodes, 10 facts, 93 active foresight

**Generated Answer:** Aru's primary hobbies include bouldering at a V5 level, surfing solo at Pacifica on weekends, and playing basketball. Their interests are deeply rooted in robotics and AI, which they explore through leading a software team and visiting tech-focused destinations like Akihabara. Aru also enjoys watching sci-fi thriller movies and researching international food tours for upcoming travels.

---

## Q7: Does Aru have any upcoming travel plans?
**Category:** Retrieval Relevance
**Expected:** Tokyo trip in August still planned.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 27.8s
**Retrieved:** 10 episodes, 10 facts, 93 active foresight

**Generated Answer:** Aru is scheduled to travel to Tokyo, Japan, in August 2026 with their friend and roommate, Jake. The planned itinerary includes visiting Akihabara, the Miraikan museum, and outdoor bouldering at Mitake. Additionally, Aru regularly travels to Pacifica on weekends for solo surfing trips to decompress.

---

## Q8: Where does Aru currently live?
**Category:** Profile Evolution
**Expected:** Palo Alto (relocated for Tesla). No longer on University Ave. Conflict should supersede.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 25.3s
**Retrieved:** 10 episodes, 10 facts, 93 active foresight

**Generated Answer:** Aru currently lives in an apartment on University Avenue in Palo Alto. They share this residence with their roommate and friend, Jake.

---

## Q9: Can Aru play sports this week?
**Category:** Foresight / Temporal
**Expected:** Yes. No sports-related injuries. Food poisoning doesn't restrict sports.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 30.7s
**Retrieved:** 10 episodes, 10 facts, 93 active foresight

**Generated Answer:** Yes, Aru can play sports this week as they have fully recovered from a torn ACL and have already resumed playing basketball and bouldering at a V5 level. Although Aru just finished a course of antibiotics for food poisoning on June 15, 2026, they are currently at 100% health and planning to surf this upcoming weekend.

---

## Q10: What do you know about Aru overall?
**Category:** Retrieval Relevance
**Expected:** Stanford student interning at Tesla Autopilot, lives in Palo Alto, non-vegetarian, surfs at Pacifica, Tokyo trip in August.
**Status:** SUFFICIENT | Rounds: 1 | Latency: 33.8s
**Retrieved:** 10 episodes, 10 facts, 93 active foresight

**Generated Answer:** Aru is a Stanford student and robotics team leader who currently works as an intern for Tesla Autopilot in Palo Alto. They are in peak physical health following an ACL recovery and stay active through V5 bouldering, surfing at Pacifica, and a high-protein diet of chicken and fish. Residing on University Avenue, Aru is currently planning an August 2026 trip to Tokyo with their roommate Jake to explore the city's robotics, food, and bouldering scenes.
