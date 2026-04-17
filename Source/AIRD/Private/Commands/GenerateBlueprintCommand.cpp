#include "Commands/GenerateBlueprintCommand.h"

#include "AIRDLog.h"
#include "AIRDBridge.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

FString FGenerateBlueprintCommand::GetName() const
{
    return TEXT("generate_blueprint");
}

bool FGenerateBlueprintCommand::Execute(const FString& PayloadJson, FString& OutMessage)
{
    TSharedPtr<FJsonObject> Payload;
    TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(PayloadJson);
    if (!FJsonSerializer::Deserialize(Reader, Payload) || !Payload.IsValid())
    {
        OutMessage = TEXT("Invalid JSON payload.");
        return false;
    }

    FString Prompt;
    if (Payload->HasField(TEXT("prompt")))
    {
        Prompt = Payload->GetStringField(TEXT("prompt"));
    }

    const bool bOk = UAIRDBridge::GenerateBlueprintFromPrompt(Prompt);
    OutMessage = bOk ? TEXT("Blueprint generated.") : TEXT("Failed to generate blueprint.");
    if (bOk)
    {
        UE_LOG(LogAIRD, Log, TEXT("generate_blueprint: prompt len=%d"), Prompt.Len());
    }
    return bOk;
}
