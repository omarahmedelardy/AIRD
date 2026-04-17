#include "Commands/MoveActorCommand.h"

#include "AIRDLog.h"
#include "AIRDBridge.h"
#include "Dom/JsonObject.h"
#include "Engine/World.h"
#include "Editor.h"
#include "EngineUtils.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

FString FMoveActorCommand::GetName() const
{
    return TEXT("move_actor");
}

bool FMoveActorCommand::Execute(const FString& PayloadJson, FString& OutMessage)
{
    TSharedPtr<FJsonObject> Payload;
    TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(PayloadJson);
    if (!FJsonSerializer::Deserialize(Reader, Payload) || !Payload.IsValid())
    {
        OutMessage = TEXT("Invalid JSON payload.");
        return false;
    }

    if (!Payload->HasField(TEXT("actor_name")))
    {
        OutMessage = TEXT("Missing 'actor_name' field.");
        return false;
    }

    const FString ActorName = Payload->GetStringField(TEXT("actor_name"));
    if (ActorName.IsEmpty())
    {
        OutMessage = TEXT("Empty 'actor_name'.");
        return false;
    }

    const TSharedPtr<FJsonObject>* LocationObj = nullptr;
    if (!Payload->TryGetObjectField(TEXT("new_location"), LocationObj) || !LocationObj || !LocationObj->IsValid())
    {
        OutMessage = TEXT("Missing or invalid 'new_location' object.");
        return false;
    }

    const FVector NewLocation(
        (*LocationObj)->GetNumberField(TEXT("x")),
        (*LocationObj)->GetNumberField(TEXT("y")),
        (*LocationObj)->GetNumberField(TEXT("z"))
    );

#if WITH_EDITOR
    UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
    if (!World)
    {
        OutMessage = TEXT("Editor world not found.");
        return false;
    }

    for (TActorIterator<AActor> It(World); It; ++It)
    {
        AActor* Actor = *It;
        if (Actor && Actor->GetName().Equals(ActorName, ESearchCase::IgnoreCase))
        {
            const bool bOk = UAIRDBridge::MoveActorToLocation(Actor, NewLocation);
            OutMessage = bOk ? TEXT("Actor moved.") : TEXT("Failed to move actor.");
            if (bOk)
            {
                UE_LOG(LogAIRD, Log, TEXT("move_actor: %s -> (%.1f, %.1f, %.1f)"), *ActorName, NewLocation.X, NewLocation.Y, NewLocation.Z);
            }
            return bOk;
        }
    }
#endif

    OutMessage = TEXT("Actor not found.");
    return false;
}
