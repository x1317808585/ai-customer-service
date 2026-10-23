"use client";

import { useCallback, useEffect, useState } from "react";

import { describeError, getCurrentUser, isUnauthorized, logout, type User } from "@/lib/api";

/**
 * 当前登录的用户。还没登录或登录已过期时，整页跳到登录页。
 *
 * 登录、退出、过期都用整页跳转而不是页内路由：浏览器会丢掉页面上的全部状态、
 * 中断所有进行中的请求，上一个用户的数据不可能残留到下一个用户的页面上。
 */
export function useCurrentUser() {
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .catch((reason: unknown) => {
        if (isUnauthorized(reason)) window.location.replace("/login");
        else setError(describeError(reason));
      });
  }, []);

  const logOut = useCallback(async () => {
    try {
      await logout();
      window.location.replace("/login");
    } catch (reason) {
      setError(`退出失败：${describeError(reason)}`);
    }
  }, []);

  return { user, error, logOut };
}
