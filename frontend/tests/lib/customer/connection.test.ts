import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { watchIdle } from "@/lib/customer/connection";

describe("watchIdle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("到时间还没收到事件，判定连接中断", () => {
    const onIdle = vi.fn();
    watchIdle(45_000, onIdle);

    vi.advanceTimersByTime(44_999);
    expect(onIdle).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onIdle).toHaveBeenCalledOnce();
  });

  it("收到事件（包括心跳）就重新计时", () => {
    const onIdle = vi.fn();
    const watch = watchIdle(45_000, onIdle);

    vi.advanceTimersByTime(40_000);
    watch.touch();
    vi.advanceTimersByTime(40_000);
    expect(onIdle).not.toHaveBeenCalled();
    vi.advanceTimersByTime(5_000);
    expect(onIdle).toHaveBeenCalledOnce();
  });

  it("回复结束停止计时后，不会再判定中断", () => {
    const onIdle = vi.fn();
    const watch = watchIdle(45_000, onIdle);

    watch.stop();
    vi.advanceTimersByTime(100_000);
    expect(onIdle).not.toHaveBeenCalled();
  });

  it("只判定一次，之后收到事件也不再重新计时", () => {
    const onIdle = vi.fn();
    const watch = watchIdle(45_000, onIdle);

    vi.advanceTimersByTime(45_000);
    watch.touch();
    vi.advanceTimersByTime(100_000);
    expect(onIdle).toHaveBeenCalledOnce();
  });
});
