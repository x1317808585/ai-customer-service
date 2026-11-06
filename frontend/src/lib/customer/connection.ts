/**
 * 回复连接是否还活着。后端生成回复时超过 15 秒没有事件就发一个心跳（见后端 app/api/conversations.py）；
 * 前端收到任何事件（包括心跳）都重新计时，超过 CONNECTION_IDLE_LIMIT_MS 什么都没收到，就按连接中断处理。
 * 45 秒大约是连续错过 3 次心跳，这是本项目的取值，不是行业标准。
 */
export const CONNECTION_IDLE_LIMIT_MS = 45_000;

export type IdleWatch = {
  /** 收到了事件：重新计时 */
  touch: () => void;
  /** 回复结束（完成、出错、停止）：清掉计时器，之后不会再触发 */
  stop: () => void;
};

/** 开始计时：limitMs 内没有调用 touch，就调用一次 onIdle。触发过或者 stop 之后，touch 不再重新计时。 */
export function watchIdle(limitMs: number, onIdle: () => void): IdleWatch {
  let timer: ReturnType<typeof setTimeout> | null = setTimeout(fire, limitMs);

  function fire() {
    timer = null;
    onIdle();
  }

  return {
    touch() {
      if (timer === null) return;
      clearTimeout(timer);
      timer = setTimeout(fire, limitMs);
    },
    stop() {
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
  };
}
