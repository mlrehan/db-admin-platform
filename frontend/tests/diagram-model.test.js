import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DiagramModel,
  addForeignKeySql,
  baseType,
  displayType,
  dropForeignKeySql,
  planRelationship,
  tableKey,
} from "../core/diagram-model.js";

const GRAPH = {
  relationships: [
    { name: "fk_tutor_manager", child_schema: "dbo", child_table: "Tutor", child_columns: ["ManagerId"],
      parent_schema: "dbo", parent_table: "Tutor", parent_columns: ["TutorId"], cardinality: "one-to-many" },
    { name: "fk_profile_tutor", child_schema: "dbo", child_table: "TutorProfile", child_columns: ["TutorId"],
      parent_schema: "dbo", parent_table: "Tutor", parent_columns: ["TutorId"], cardinality: "one-to-one" },
    { name: "fk_tc_tutor", child_schema: "dbo", child_table: "TutorCourse", child_columns: ["TutorId"],
      parent_schema: "dbo", parent_table: "Tutor", parent_columns: ["TutorId"], cardinality: "one-to-many" },
    { name: "fk_tc_course", child_schema: "dbo", child_table: "TutorCourse", child_columns: ["CourseCode"],
      parent_schema: "dbo", parent_table: "Course", parent_columns: ["CourseCode"], cardinality: "one-to-many" },
  ],
  junctions: [{ schema_name: "dbo", table: "TutorCourse", parents: [["dbo", "Tutor"], ["dbo", "Course"]] }],
};

test("double-click / repeated adds reserve exactly one node (the reported bug)", async () => {
  const m = new DiagramModel();
  // Simulate the race: two clicks arrive before the metadata request resolves.
  const first = m.reserve("dbo", "Admission");
  const second = m.reserve("dbo", "Admission");
  const third = m.reserve("DBO", "admission"); // case-insensitive identity
  assert.equal(first.added, true);
  assert.equal(second.added, false);
  assert.equal(third.added, false);
  assert.equal(m.nodes.size, 1);
  await Promise.resolve();
  m.resolve(first.key, { columns: [] });
  assert.equal(m.nodes.get(first.key).status, "ready");
});

test("an added table can always be removed, and re-added afterwards", () => {
  const m = new DiagramModel();
  const { key } = m.reserve("dbo", "Student");
  assert.equal(m.remove(key), true);
  assert.equal(m.nodes.size, 0);
  assert.equal(m.remove(key), false);
  assert.equal(m.reserve("dbo", "Student").added, true);
});

test("metadata that arrives after removal is ignored; a failed load leaves no ghost", () => {
  const m = new DiagramModel();
  const { key } = m.reserve("dbo", "Room");
  m.remove(key);
  assert.equal(m.resolve(key, { columns: [] }), false);
  assert.equal(m.nodes.size, 0);
  const b = m.reserve("dbo", "Course");
  m.fail(b.key);
  assert.equal(m.nodes.size, 0);
});

test("edges: FK connectors with cardinality, self-reference, only between tables on the canvas", () => {
  const m = new DiagramModel();
  m.setRelationships(GRAPH);
  m.reserve("dbo", "Tutor");
  let edges = m.edges();
  assert.deepEqual(edges.map((e) => [e.name, e.kind, e.self]), [["fk_tutor_manager", "one-to-many", true]]);
  m.reserve("dbo", "TutorProfile");
  edges = m.edges();
  assert.ok(edges.some((e) => e.name === "fk_profile_tutor" && e.kind === "one-to-one"));
});

test("many-to-many: logical M:N connector when the junction table is off the canvas", () => {
  const m = new DiagramModel();
  m.setRelationships(GRAPH);
  m.reserve("dbo", "Tutor");
  m.reserve("dbo", "Course");
  const mn = m.edges().filter((e) => e.kind === "many-to-many");
  assert.equal(mn.length, 1);
  assert.equal(mn[0].via, "TutorCourse");
  // Once the junction table itself is added, its two physical 1:N FKs replace the logical line.
  m.reserve("dbo", "TutorCourse");
  const kinds = m.edges().filter((e) => e.child === tableKey("dbo", "TutorCourse")).map((e) => e.kind);
  assert.deepEqual(kinds.sort(), ["one-to-many", "one-to-many"]);
  assert.equal(m.edges().filter((e) => e.kind === "many-to-many").length, 0);
  assert.ok(m.isJunction(tableKey("dbo", "TutorCourse")));
});

test("connectors can be hidden from the diagram and restored", () => {
  const m = new DiagramModel();
  m.setRelationships(GRAPH);
  m.reserve("dbo", "Tutor");
  m.reserve("dbo", "TutorProfile");
  const id = m.edges().find((e) => e.name === "fk_profile_tutor").id;
  m.hideEdge(id);
  assert.ok(!m.edges().some((e) => e.id === id));
  m.showAllEdges();
  assert.ok(m.edges().some((e) => e.id === id));
});

test("related tables and layered auto-layout (parents left of children)", () => {
  const m = new DiagramModel();
  m.setRelationships(GRAPH);
  m.reserve("dbo", "TutorCourse");
  m.reserve("dbo", "Tutor");
  m.reserve("dbo", "Course");
  assert.deepEqual(m.relatedKeys(tableKey("dbo", "Tutor")).sort(), ["dbo.tutorcourse", "dbo.tutorprofile"]);
  m.autoLayout();
  const x = (n) => m.nodes.get(tableKey("dbo", n)).x;
  assert.ok(x("Tutor") < x("TutorCourse") && x("Course") < x("TutorCourse"));
});

test("serialize keeps positions and hidden connectors", () => {
  const m = new DiagramModel();
  const { key } = m.reserve("dbo", "Room", { x: 10, y: 20 });
  m.move(key, 300.4, 120.6);
  assert.deepEqual(m.serialize(), { nodes: [{ schema: "dbo", name: "Room", x: 300, y: 121 }], hidden: [] });
});

test("relationship planning: key column is the parent; types compared across dialects", () => {
  const pk = { schema: "dbo", table: "Room", column: "RoomNo", type: "INT", isKey: true };
  const fk = { schema: "dbo", table: "Course", column: "RoomNo", type: "integer", isKey: false };
  const plan = planRelationship(fk, pk);
  assert.equal(plan.ok, true);
  assert.equal(plan.parent.table, "Room");
  assert.equal(plan.warnings.length, 0);
  assert.equal(planRelationship(pk, fk).parent.table, "Room"); // either drag direction
  const bad = planRelationship({ ...fk, isKey: false }, { ...pk, isKey: false });
  assert.equal(bad.ok, false);
  const mismatch = planRelationship({ ...fk, type: "varchar(10)" }, pk);
  assert.equal(mismatch.warnings.length, 1);
  assert.equal(baseType('VARCHAR(32) COLLATE "SQL_Latin1_General_CP1_CI_AS"'), "varchar");
  assert.equal(displayType('VARCHAR(32) COLLATE "SQL_Latin1_General_CP1_CI_AS"'), "VARCHAR(32)");
});

test("FK DDL per dialect (and SQLite reported as unsupported)", () => {
  const spec = {
    child: { schema: "dbo", table: "Course", column: "RoomNo" },
    parent: { schema: "dbo", table: "Room", column: "RoomNo" },
    name: "FK_Course_Room", onDelete: "CASCADE", onUpdate: "NO ACTION",
  };
  assert.equal(addForeignKeySql("mssql", spec),
    "ALTER TABLE [dbo].[Course]\n  ADD CONSTRAINT [FK_Course_Room] FOREIGN KEY ([RoomNo])\n  REFERENCES [dbo].[Room] ([RoomNo]) ON DELETE CASCADE;");
  assert.match(addForeignKeySql("postgresql", spec), /^ALTER TABLE "dbo"\."Course"/);
  assert.match(addForeignKeySql("mysql", spec), /^ALTER TABLE `Course`\n {2}ADD CONSTRAINT `FK_Course_Room`/);
  assert.equal(addForeignKeySql("sqlite", spec), null);
  assert.equal(dropForeignKeySql("mysql", { childSchema: "lait", childTable: "Course", name: "fk_x" }),
    "ALTER TABLE `Course` DROP FOREIGN KEY `fk_x`;");
  assert.equal(dropForeignKeySql("mssql", { childSchema: "dbo", childTable: "Course", name: "fk_x" }),
    "ALTER TABLE [dbo].[Course] DROP CONSTRAINT [fk_x];");
  assert.equal(dropForeignKeySql("sqlite", { childTable: "Course", name: "fk_x" }), null);
  // Identifiers with quote characters are escaped, never injected.
  assert.match(addForeignKeySql("mssql", { ...spec, name: "x]; DROP TABLE t; --" }), /\[x\]\]; DROP TABLE t; --\]/);
});
