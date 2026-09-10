import { ConversationProvider } from "@elevenlabs/react";
import { InspectionConsole } from "./InspectionConsole";

export function App() {
  return (
    <ConversationProvider onError={(error) => console.error("Conversation error:", error)}>
      <InspectionConsole />
    </ConversationProvider>
  );
}
