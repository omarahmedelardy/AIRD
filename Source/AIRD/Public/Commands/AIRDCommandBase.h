#pragma once

#include "CoreMinimal.h"

class AIRD_API FAIRDCommandBase
{
public:
    virtual ~FAIRDCommandBase() = default;
    virtual FString GetName() const = 0;
    virtual bool Execute(const FString& PayloadJson, FString& OutMessage) = 0;
};
