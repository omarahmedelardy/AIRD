#include "Commands/SpawnActorCommand.h"

#include "AIRDLog.h"
#include "AIRDBridge.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace AIRDCommand
{
    static constexpr double DefaultSpawnZ = 100.0;
}

FString FSpawnActorCommand::GetName() const
{
    return TEXT("spawn_actor");
}

bool FSpawnActorCommand::Execute(const FString& PayloadJson, FString& OutMessage)
{
    TSharedPtr<FJsonObject> Payload;
    TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(PayloadJson);
    if (!FJsonSerializer::Deserialize(Reader, Payload) || !Payload.IsValid())
    {
        OutMessage = TEXT("Invalid JSON payload.");
        return false;
    }

    if (!Payload->HasField(TEXT("description")))
    {
        OutMessage = TEXT("Missing 'description' field.");
        return false;
    }

    const FString Description = Payload->GetStringField(TEXT("description"));
    if (Description.IsEmpty())
    {
        OutMessage = TEXT("Empty 'description'.");
        return false;
    }

    const TSharedPtr<FJsonObject>* LocationObj = nullptr;
    Payload->TryGetObjectField(TEXT("location"), LocationObj);

    const FVector Location(
        LocationObj && LocationObj->IsValid() ? (*LocationObj)->GetNumberField(TEXT("x")) : 0.0,
        LocationObj && LocationObj->IsValid() ? (*LocationObj)->GetNumberField(TEXT("y")) : 0.0,
        LocationObj && LocationObj->IsValid() ? (*LocationObj)->GetNumberField(TEXT("z")) : AIRDCommand::DefaultSpawnZ
    );

    const bool bOk = UAIRDBridge::SpawnActorFromDescription(Description, Location);
    OutMessage = bOk ? TEXT("Actor spawned successfully.") : TEXT("Failed to spawn actor.");
    if (bOk)
    {
        UE_LOG(LogAIRD, Log, TEXT("spawn_actor: %s at (%.1f, %.1f, %.1f)"), *Description, Location.X, Location.Y, Location.Z);
    }
    return bOk;
}
