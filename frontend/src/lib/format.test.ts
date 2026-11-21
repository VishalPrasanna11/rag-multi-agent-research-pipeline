import { describe, expect, it } from "vitest";
import { fmtValue, signed } from "./format";

describe("format", () => {
  it("formats units the same way as the API", () => {
    expect(fmtValue(57_000_000_000, "USD")).toBe("$57.00B");
    expect(fmtValue(2.684, "USD/share")).toBe("$2.68");
    expect(fmtValue(-2.94, "%")).toBe("−2.9%");
    expect(fmtValue(0.42, "prob")).toBe("42%");
    expect(fmtValue(null, "USD")).toBe("n/a");
  });
  it("never relies on colour alone: signed() carries the sign", () => {
    expect(signed(3.21)).toBe("+3.2%");
    expect(signed(-3.21)).toBe("−3.2%");
  });
});
