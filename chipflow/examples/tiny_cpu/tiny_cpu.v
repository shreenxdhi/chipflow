// Minimal instructional accumulator: LOAD, ADD, SUB, HALT. Not a software ISA platform.
module tiny_cpu(input clk, input reset, output reg [7:0] accumulator,
                output reg done, output reg [3:0] pc);
  reg [9:0] instruction;
  always @* begin
    case(pc)
      0: instruction = {2'b00,8'd5};
      1: instruction = {2'b01,8'd3};
      2: instruction = {2'b10,8'd1};
      default: instruction = {2'b11,8'd0};
    endcase
  end
  always @(posedge clk) begin
    if(reset) begin accumulator <= 0; done <= 0; pc <= 0; end
    else if(!done) begin
      case(instruction[9:8])
        2'b00: accumulator <= instruction[7:0];
        2'b01: accumulator <= accumulator + instruction[7:0];
        2'b10: accumulator <= accumulator - instruction[7:0];
        2'b11: done <= 1;
      endcase
      if(instruction[9:8] != 2'b11) pc <= pc + 1'b1;
    end
  end
endmodule
