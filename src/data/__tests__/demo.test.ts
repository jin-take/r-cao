import { describe, expect, it } from "vitest";
import { demoAgents, demoTasks } from "../demo";
import { demoOperations } from "../operations";

describe("Owner Console read models", () => {
  it("keeps named Agents and Owner-issued task fixtures auditable", () => {
    expect(demoAgents.every((agent) => agent.name.length > 0)).toBe(true);
    expect(demoTasks.filter((task) => task.issuedBy === "owner-local")).not.toHaveLength(0);
  });

  it("keeps searchable operation references attached to task or run context", () => {
    expect(demoOperations.every((record) => record.taskId || record.runId || record.refs.length > 0)).toBe(true);
  });
});
