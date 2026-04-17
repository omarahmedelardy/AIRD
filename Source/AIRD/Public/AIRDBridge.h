#pragma once

#include "Kismet/BlueprintFunctionLibrary.h"
#include "AIRDBridge.generated.h"

class AActor;

UCLASS()
class AIRD_API UAIRDBridge : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintCallable, Category = "AIRD|Bridge")
    static TArray<AActor*> GetAllActorsInWorld();

    UFUNCTION(BlueprintCallable, Category = "AIRD|Bridge")
    static FString GetActorsAsJSON();

    UFUNCTION(BlueprintCallable, Category = "AIRD|Bridge")
    static bool CaptureViewportScreenshot(FString& OutBase64);

    UFUNCTION(BlueprintCallable, Category = "AIRD|Bridge")
    static bool SpawnActorFromDescription(const FString& Description, FVector Location);

    UFUNCTION(BlueprintCallable, Category = "AIRD|Bridge")
    static bool MoveActorToLocation(AActor* Actor, FVector NewLocation);

    UFUNCTION(BlueprintCallable, Category = "AIRD|Bridge")
    static bool GenerateBlueprintFromPrompt(const FString& Prompt);

    UFUNCTION(BlueprintCallable, Category = "AIRD|Bridge")
    static bool AddBlueprintVariable(
        const FString& BlueprintPath,
        const FString& VariableName,
        const FString& VariableType
    );

    UFUNCTION(BlueprintCallable, Category = "AIRD|Bridge")
    static bool AddBlueprintFunction(
        const FString& BlueprintPath,
        const FString& FunctionName
    );

    UFUNCTION(BlueprintCallable, Category = "AIRD|Bridge")
    static FString GetLastBlueprintEditError();

    UFUNCTION(BlueprintCallable, Category = "AIRD|Bridge")
    static bool ExecutePythonCommand(const FString& Command);
};
