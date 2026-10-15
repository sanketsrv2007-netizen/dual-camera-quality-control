const int ledPin = 13;

void setup()
{
    Serial.begin(9600);
    pinMode(ledPin, OUTPUT);
}

void loop()
{
    if (Serial.available())
    {
        char command = Serial.read();

        if (command == '1')
        {
            digitalWrite(ledPin, HIGH);
            Serial.println("LED ON");
        }
        else if (command == '0')
        {
            digitalWrite(ledPin, LOW);
            Serial.println("LED OFF");
        }
    }
}