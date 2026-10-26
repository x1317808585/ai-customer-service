"use client";

import { ChatScreen } from "@/components/customer/ChatScreen";
import { useCurrentUser } from "@/hooks/useCurrentUser";

export default function ChatPage() {
  const { user, error, logOut } = useCurrentUser();

  if (error) {
    return <p role="alert" className="p-6 text-sm text-red-700">{error}</p>;
  }
  if (!user) {
    return null;
  }
  return <ChatScreen user={user} onLogOut={logOut} />;
}
