// Register-array storage; not a technology SRAM macro or qualified memory view.
module fifo_memory #(parameter WIDTH=8, DEPTH=4, ADDR=2)(
  input clk, input write_enable,
  input [ADDR-1:0] write_address, read_address,
  input [WIDTH-1:0] data_in, output [WIDTH-1:0] data_out
);
  reg [WIDTH-1:0] memory [0:DEPTH-1];
  assign data_out = memory[read_address];
  always @(posedge clk) begin
    if(write_enable) memory[write_address] <= data_in;
  end
endmodule
